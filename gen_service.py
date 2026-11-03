#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成相关的业务逻辑（从 server.py 下沉出来）。

这里不碰 HTTP：每个入口都是 `f(body, send) -> (status, payload)`，
send 由路由层传进来（server 的 self._send），所以返回值的形状和以前完全一致。
"""

import json
import threading
import time

import gen_cache
import job_store
import lc_tools

from common import as_bool, as_float, as_int, load_config, log

from dict_db import local_concepts, validate_tags

from llm_client import DEFAULT_MODEL, call_llm, get_model, is_known_model

from prompts import DEFAULT_NEGATIVE, TAG_CN

from agent_core import (_CONCEPT_MIN_INJECT, _clean_prompt, agent_generate, concept_injection_block,
                        ensure_frame, map_concepts, natural_fields,
                        negative_space_conflict_trace, plan_generate,
                        plan_prefetch_detail, prefetch_trace_summary,
                        prompt_style_warnings, reorder_hits,
                        resolve_negative_space_conflict, self_refine_natural,
                        self_refine_rules, word_count)

JOB_KEEP = job_store.JOB_KEEP   # 只保留最近这么多个任务，避免越攒越多


def job_new(text="", prompt_model="", kwargs=None):
    return job_store.new(text=text, prompt_model=prompt_model, kwargs=kwargs)


def job_step(jid, msg, pct=None):
    """给任务追加一行进度，并更新百分比（进度条用）。"""
    job_store.step(jid, msg, pct)


def job_finish(jid, result=None, error=None):
    job_store.finish(jid, result=result, error=error)


def job_meta(jid, **fields):
    return job_store.meta(jid, **fields)


def job_get(jid):
    j = job_store.get(jid)
    if not j:
        return None
    # 接口保持原来的形状：steps 是给人看的一行行文字（前端/测试都按这个用），
    # 磁盘上存的是带 pct/ts 的对象，这里把明细另放 step_details。
    details = j.get("steps") or []
    j["step_details"] = details
    j["steps"] = [d.get("msg") if isinstance(d, dict) else str(d) for d in details]
    return j


def _report_stats(res):
    """统一打一行任务统计到日志。"""
    st = res.get("stats") or {}
    rv = res.get("review") or {}
    if res.get("mode") == "natural":
        log("Krea2：%d 次模型调用 · %d 次工具调用 · %d 词 · 自省 %s 轮%s" % (
            st.get("llm_calls", 0), st.get("tool_calls", 0), st.get("words", 0),
            rv.get("iterations", 0),
            ("（%s 分）" % rv.get("final_score"))
            if rv.get("final_score") is not None else ""))
        return
    log("智能体：%d 次模型调用 · %d 次工具调用 · 自省 %s 轮%s%s" % (
        st.get("llm_calls", 0), st.get("tool_calls", 0), rv.get("iterations", 0),
        ("（%s 分）" % rv.get("final_score"))
        if rv.get("final_score") is not None else "",
        (" · 概念确认 %s / 未确认 %s" % (st.get("concepts_confirmed"),
                                      st.get("concepts_unknown")))
        if st.get("concepts_confirmed") is not None else ""))


def _start_async_job(text, prompt_model, kwargs, cfg):
    """起一个后台任务：新任务落盘，每一步进度也落盘，返回任务号。"""
    jid = job_new(text=text, prompt_model=prompt_model, kwargs=kwargs)
    job_step(jid, "任务已创建，正在准备提示词")

    natural = get_model(prompt_model)["mode"] == "natural"
    akind = "krea" if natural else "agent"
    ckey = ""
    if gen_cache.enabled(cfg):
        opts = {k: v for k, v in kwargs.items() if k != "prompt_model"}
        ckey = gen_cache.make_key(akind, text, cfg, prompt_model, **opts)

    def _work():
        try:
            if ckey:
                hit = gen_cache.get(ckey)
                if hit:
                    hit["cached"] = True
                    job_step(jid, "命中缓存，直接返回")
                    job_finish(jid, result=hit)
                    log("智能体命中缓存：%s…" % text[:16])
                    return
            def _emit(ev):
                if isinstance(ev, dict) and ev.get("type") == "plan":
                    job_store.meta(jid, plan=ev.get("plan"))

            res = agent_generate(cfg, text,
                                 progress=lambda m, p=None: job_step(jid, m, p),
                                 emit=_emit,
                                 **kwargs)
            if ckey and not res.get("error"):
                gen_cache.put(ckey, akind, res)
            job_finish(jid, result=res)
            _report_stats(res)
        except Exception as e:  # noqa: BLE001
            job_finish(jid, error=str(e))
            log("智能体任务失败：%s" % e)

    threading.Thread(target=_work, daemon=True).start()
    return jid



def _apply_api_overrides(cfg, body):
    """界面上填的临时配置优先：api 覆盖 + 辅助模型名（空 = 用主模型）。

    tag 模式的概念核对没有开关，始终开启；Krea 模式不受影响。
    """
    ui = body.get("api") or {}
    for k in ("api_base", "api_key", "model"):
        if (ui.get(k) or "").strip():
            cfg[k] = ui[k].strip()
    if "aux_model" in body:
        cfg["aux_model"] = str(body.get("aux_model") or "").strip()
    elif (ui.get("aux_model") or "").strip():
        cfg["aux_model"] = ui["aux_model"].strip()
    return cfg


def _reflect_rounds_of(body, natural):
    """自省轮数：Krea 2 默认 4 轮，tag 模式默认 2 轮；显式传 0 就是关闭。

    传了非法值（"abc"、负数、超大数）不让它抛异常，按默认值处理并夹到 0~6。
    """
    default = 4 if natural else 2
    if body.get("reflect_rounds") is None:
        return default
    return as_int(body.get("reflect_rounds"), default, 0, 6)


def _single_concept_parts(concepts):
    """单次生成的概念核对：概念已经由本地词表索引抽好，这里只做映射 + 注入段。

    纯本地、不调模型；确认数不足 3 个时不注入（和 Agent 模式口径一致），
    只记统计与轨迹。抽不到概念时返回 0/0 和一条说明，绝不报错。
    """
    if not concepts:
        return ({"concepts_confirmed": 0, "concepts_unknown": 0}, "",
                [{"round": 0, "tool": "概念", "args": {},
                  "summary": "本地概念抽取：0 个"}])
    mapping = map_concepts(concepts)
    stats = {"concepts_confirmed": len(mapping["confirmed"]),
             "concepts_unknown": len(mapping["unknown"])}
    block = concept_injection_block(mapping)
    if block:
        return stats, block, []
    return (stats, "",
            [{"round": 0, "tool": "概念", "args": {},
              "summary": "概念确认不足 %d 个，已跳过注入，继续生成"
                         % _CONCEPT_MIN_INJECT}])



def generate_single(body, send):
    """单次生成（/api/generate）。"""
    text = (body.get("text") or "").strip()
    if not text:
        return send(400, {"error": "请先输入中文描述"})
    cfg = load_config()
    # 界面上填的临时配置优先（含辅助模型名）
    _apply_api_overrides(cfg, body)
    if not (cfg.get("api_key") or "").strip():
        return send(400, {
            "error": "还没有填 API Key。请在右上角「模型设置」里填写，"
                     "或复制 config.example.json 为 config.json 后填入。"
        })
    pm = body.get("prompt_model") or DEFAULT_MODEL
    if not is_known_model(pm):
        return send(400, {"error": "不认识的模型：%s" % pm})
    extra_rules = (body.get("extra_rules") or "")
    # 入参一律宽容解析：max_tags="abc" / temperature=null 这类不该让请求 500
    max_tags = as_int(body.get("max_tags"), 45, 1, 200)
    temperature = as_float(body.get("temperature"), 0.6, 0.0, 2.0)
    sys_prompt = (body.get("system_prompt") or "")
    natural = get_model(pm)["mode"] == "natural"
    plan_flag = bool(body.get("plan", False))
    aux = (cfg.get("aux_model") or "").strip() or (cfg.get("model") or "").strip()
    ckey = ""
    if gen_cache.enabled(cfg):
        ckey = gen_cache.make_key("single", text, cfg, pm, max_tags=max_tags,
                                  temperature=temperature, extra_rules=extra_rules,
                                  system_prompt=sys_prompt,
                                  plan=plan_flag)
        hit = gen_cache.get(ckey)
        if hit:
            hit["cached"] = True
            log("命中缓存：%s…" % text[:16])
            return send(200, hit)
    # 概念核对（tag 模式始终开启）/ 规划展示：单次生成也统一用本地概念抽取，
    # 不再为概念单独调一次模型（计划展示仍然要一次计划调用）。
    # 失败只记轨迹、不中断。
    plan_info, plan_trace = None, []
    prefetch_trace = []
    conflict_trace = []
    concept_trace, concept_stats, concept_block = [], {}, ""
    if plan_flag:
        plan_info, plan_err = plan_generate(
            cfg, text, natural=natural, temperature=temperature, model=aux)
        if not plan_info:
            plan_trace.append({"round": 0, "tool": "计划", "args": {},
                               "summary": "计划没生成出来（%s），继续生成" % plan_err})
    if not natural:
        # 概念抽取纯本地（词表中文索引最长匹配），和 Agent 模式同一套口径
        concept_stats, concept_block, concept_trace = _single_concept_parts(
            local_concepts(text))
    # 计划预查：本地查词表，把结果拼进注入段（不调模型、加了也不算调用）。
    # Krea2 不做（沿用「计划只展示」）；轨迹里记一条，前端照常渲染。
    if plan_info and not natural:
        detail = plan_prefetch_detail(plan_info.get("queries") or [])
        if detail["block"]:
            concept_block = detail["block"] + (concept_block or "")
            prefetch_trace.append({"round": 0, "tool": "计划预查", "args": {},
                                   "summary": prefetch_trace_summary(detail)})
    try:
        content, parsed, usage = call_llm(
            cfg, text,
            extra_rules=extra_rules,
            max_tags=max_tags,
            temperature=temperature,
            prompt_model=pm,
            system_prompt=sys_prompt,
            concept_block=concept_block,
        )
    except Exception as e:  # noqa: BLE001
        return send(500, {"error": str(e)})

    if get_model(pm)["mode"] == "natural":
        if not parsed or not isinstance(parsed, dict):
            res = {"raw": content, "parsed": None, "mode": "natural",
                   "error": "模型没有返回合法 JSON，已把原文给你，可手动整理"}
        else:
            fields = natural_fields(parsed, content)
            if not fields["prompt"]:
                res = {"raw": content, "parsed": parsed, "mode": "natural",
                       "error": "模型没有给出英文提示词，已把原文给你"}
            else:
                res = {
                    "mode": "natural",
                    "raw": content,
                    "prompt": fields["prompt"],
                    "notes": fields["notes"],
                    "stats": {"llm_calls": 1, "tool_calls": 0,
                              "words": word_count(fields["prompt"]),
                              "warnings": prompt_style_warnings(fields["prompt"])},
                    "usage": usage,
                }
    elif not parsed or not isinstance(parsed, dict):
        res = {"raw": content, "parsed": None,
               "positive_candidates": [],
               "error": "模型没有返回合法 JSON，已把原文给你，可手动整理"}
    else:
        pos = [str(t).strip() for t in (parsed.get("positive") or []) if str(t).strip()]
        valid, unknown = validate_tags(pos)
        # 服务端硬约束：正向只留词表确认过的；不通过的进 positive_unknown。
        # 再按固定分组排好序（确定性排序，不靠模型）。
        valid = reorder_hits(valid)
        # 构图冲突：留白 vs 广角/全身 → 留白移入候选补充，不进正向
        keep, moved = resolve_negative_space_conflict(valid)
        if moved:
            valid = keep
            conflict_trace.append(negative_space_conflict_trace())
        # 框架补全：人数 / 景别 / 质量词（本地规则）
        valid, frame_added = ensure_frame(valid, text)
        valid = reorder_hits(valid)
        parsed["positive"] = [h["tag"] for h in valid]
        res = {
            "parsed": parsed,
            "raw": content,
            "positive": valid,
            "positive_unknown": unknown,
            # 单次生成没有自省改写，候选补充只可能是构图冲突搬过来的词
            "positive_candidates": moved,
            "negative": [],
            "negative_text": DEFAULT_NEGATIVE,
            "negative_tags": [{"tag": t.strip(), "cn": TAG_CN.get(t.strip(), "")}
                              for t in DEFAULT_NEGATIVE.split(",") if t.strip()],
            "negative_unknown": [],
            "notes": parsed.get("notes", ""),
            "usage": usage,
        }

    if plan_info:
        res["plan"] = plan_info
    trace_extra = plan_trace + prefetch_trace + concept_trace + conflict_trace
    if trace_extra:
        res["trace"] = trace_extra
    if plan_flag or concept_stats:
        # 生成本身 1 次；开了计划展示再 +1（概念核对纯本地，不算调用）
        if not isinstance(res.get("stats"), dict):
            res["stats"] = {"llm_calls": 1, "tool_calls": 0}
        if plan_flag:
            res["stats"]["llm_calls"] = res["stats"].get("llm_calls", 1) + 1
        if concept_stats:
            res["stats"].update(concept_stats)
    if ckey and not res.get("error"):
        gen_cache.put(ckey, "single", res)
    return send(200, res)



def reflect(body, send):
    """只重跑结构校验（/api/reflect）。"""
    # 只重跑自省：接口繁忙导致自省失败时，用它补救，不用重新生成整套 tag
    text = (body.get("text") or "").strip()
    cfg = load_config()
    _apply_api_overrides(cfg, body)
    if not (cfg.get("api_key") or "").strip():
        return send(400, {"error": "还没有填 API Key"})

    pm = body.get("prompt_model") or DEFAULT_MODEL
    if not is_known_model(pm):
        return send(400, {"error": "不认识的模型：%s" % pm})
    if get_model(pm)["mode"] == "natural":
        raw_prompt = body.get("prompt") or body.get("positive") or ""
        if isinstance(raw_prompt, list):
            raw_prompt = " ".join(str(x) for x in raw_prompt if str(x).strip())
        prompt = _clean_prompt(raw_prompt)
        if not prompt:
            return send(400, {"error": "没有可评审的提示词"})
        result = {"mode": "natural", "prompt": prompt,
                  "stats": {"llm_calls": 0, "tool_calls": 0,
                            "words": word_count(prompt)}}
        try:
            review = self_refine_natural(cfg, text, result,
                                         rounds=_reflect_rounds_of(body, True))
        except Exception as e:  # noqa: BLE001
            return send(500, {"error": str(e)})
        log("重跑 Krea2 自省：%s 轮%s" % (
            review.get("iterations"),
            ("（%s 分）" % review.get("final_score"))
            if review.get("final_score") is not None else ""))
        return send(200, {
            "mode": "natural", "review": review,
            "prompt": result["prompt"],
            "stats": {"llm_calls": result["stats"]["llm_calls"],
                      "tool_calls": 0,
                      "words": word_count(result["prompt"]),
                      "warnings": prompt_style_warnings(result["prompt"])},
        })

    pos = [str(t).strip() for t in (body.get("positive") or []) if str(t).strip()]
    if not pos:
        return send(400, {"error": "没有可评审的 tag"})
    valid, unknown = validate_tags(pos)
    result = {"positive": valid, "positive_unknown": unknown,
              "positive_candidates": [],
              "negative": [], "negative_text": DEFAULT_NEGATIVE,
              "negative_tags": [{"tag": t.strip(), "cn": TAG_CN.get(t.strip(), "")}
                                for t in DEFAULT_NEGATIVE.split(",") if t.strip()],
              "negative_unknown": [],
              "stats": {"llm_calls": 0, "tool_calls": 0}}
    try:
        review = self_refine_rules(cfg, text, result)
    except Exception as e:  # noqa: BLE001
        return send(500, {"error": str(e)})
    log("重跑自省：%s 轮%s" % (review.get("iterations"),
                            ("（%s 分）" % review.get("final_score"))
                            if review.get("final_score") is not None else ""))
    return send(200, {
        "review": review,
        "positive": result["positive"], "positive_unknown": result["positive_unknown"],
        "positive_candidates": result.get("positive_candidates") or [],
        "negative": [], "negative_text": DEFAULT_NEGATIVE,
        "negative_tags": [{"tag": t.strip(), "cn": TAG_CN.get(t.strip(), "")}
                          for t in DEFAULT_NEGATIVE.split(",") if t.strip()],
        "negative_unknown": [],
        "stats": result["stats"],
    })



def agent(body, send):
    """智能体生成，支持 async 任务（/api/agent）。"""
    text = (body.get("text") or "").strip()
    if not text:
        return send(400, {"error": "请先输入中文描述"})
    cfg = load_config()
    _apply_api_overrides(cfg, body)
    if not (cfg.get("api_key") or "").strip():
        return send(400, {
            "error": "还没有填 API Key。请在「设置」页填写，"
                     "或复制 config.example.json 为 config.json 后填入。"
        })
    pm = body.get("prompt_model") or DEFAULT_MODEL
    if not is_known_model(pm):
        return send(400, {"error": "不认识的模型：%s" % pm})
    natural = get_model(pm)["mode"] == "natural"
    kwargs = dict(
        extra_rules=(body.get("extra_rules") or ""),
        max_tags=as_int(body.get("max_tags"), 45, 1, 200),
        temperature=as_float(body.get("temperature"), 0.6, 0.0, 2.0),
        max_rounds=as_int(body.get("max_rounds"), 6, 1, 20),
        reflect=as_bool(body.get("reflect"), True),
        reflect_rounds=_reflect_rounds_of(body, natural),
        use_tools=as_bool(body.get("use_tools"), True),
        prompt_model=pm,
        system_prompt=(body.get("system_prompt") or ""),
        plan=as_bool(body.get("plan"), False),
        aux_model=(cfg.get("aux_model") or "").strip(),
        fallback=as_bool(body.get("fallback"), True),
        compress=as_bool(body.get("compress"), True),
    )
    akind = "krea" if natural else "agent"
    ckey = ""
    if gen_cache.enabled(cfg):
        opts = {k: v for k, v in kwargs.items() if k != "prompt_model"}
        ckey = gen_cache.make_key(akind, text, cfg, pm, **opts)

    # async=true：立刻返回任务号，进度和结果都从 /api/agent/progress 拿。
    # 这样界面能看到每一步在干什么，也不会因为一次请求太久而超时。
    if body.get("async"):
        jid = _start_async_job(text, pm, kwargs, cfg)
        return send(200, {"job_id": jid})

    if ckey:
        hit = gen_cache.get(ckey)
        if hit:
            hit["cached"] = True
            log("智能体命中缓存：%s…" % text[:16])
            return send(200, hit)
    try:
        res = agent_generate(cfg, text, **kwargs)
    except Exception as e:  # noqa: BLE001
        return send(500, {"error": str(e)})
    if ckey and not res.get("error"):
        gen_cache.put(ckey, akind, res)
    _report_stats(res)
    return send(200, res)



def agent_retry(body, send):
    """用落盘参数重开任务（/api/agent/retry）。"""
    # 用落盘的 text / kwargs 重开一个任务（新 id），原任务保留。
    # API Key 从不落盘，重试时用当前配置里的 Key。
    old_id = (body.get("id") or "").strip()
    old = job_get(old_id)
    if not old:
        return send(404, {"error": "任务不存在或已过期"})
    if old.get("status") == "running":
        return send(409, {"error": "任务还在运行，不用重试"})
    text = (old.get("text") or "").strip()
    if not text:
        return send(400, {"error": "这个任务没有保存描述，不能重试"})
    pm = (old.get("prompt_model") or "").strip() or DEFAULT_MODEL
    if not is_known_model(pm):
        return send(400, {"error": "不认识的模型：%s" % pm})
    cfg = load_config()
    if not (cfg.get("api_key") or "").strip():
        return send(400, {"error": "还没有填 API Key，无法重试"})
    kwargs = job_store.clean_kwargs(old.get("kwargs") or {})
    kwargs.pop("strict", None)   # 旧任务可能存过已删除的开关，重试时丢掉
    jid = _start_async_job(text, pm, kwargs, cfg)
    log("重试被中断的任务 %s → 新任务 %s" % (old_id, jid))
    return send(200, {"job_id": jid})
