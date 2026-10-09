// TagCraft 启动器（双击运行）
//
// 作用：用找到的 Python **无窗口**启动 server.py。
//   - UseShellExecute=false → 不走 shell，没有"无法验证发布者"弹窗
//   - CreateNoWindow=true   → 不弹黑框，服务在后台静默运行
//   - 界面左侧「退出」按钮 = 停止服务（POST /api/shutdown）
//
// Python 查找：按顺序列出候选，**每个都实际跑一次 `-c import sys` 验证**，
// 第一个能用的才采用（PATH 里可能有 WindowsApps 商店别名这类不能用的 python）。
// 启动过程写入 %LOCALAPPDATA%\TagCraft\launcher.log，出问题好排查。
//
// 项目目录查找：exe 同目录 → launcher.ini → 默认路径 → 「文档」下三层内搜 tagcraft
//
// 重新编译：
//   C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe ^
//       /nologo /target:winexe /out:TagCraft.exe ^
//       /win32icon:assets\tagcraft.ico /r:System.Windows.Forms.dll launcher.cs

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Launcher
{
    private static readonly string LogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TagCraft", "launcher.log");

    private static string _logTarget = null;

    private static void Log(string msg)
    {
        string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + msg + Environment.NewLine;
        if (_logTarget != null)
        {
            try { File.AppendAllText(_logTarget, line, Encoding.UTF8); return; }
            catch { _logTarget = null; }
        }
        // 依次尝试：%LOCALAPPDATA%\TagCraft → exe 同目录（受限环境下的兜底）
        var targets = new List<string>();
        try
        {
            targets.Add(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "TagCraft", "launcher.log"));
        }
        catch { }
        try
        {
            targets.Add(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "launcher.log"));
        }
        catch { }
        foreach (string t in targets)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(t));
                File.AppendAllText(t, line, Encoding.UTF8);
                _logTarget = t;
                return;
            }
            catch { }
        }
    }

    // ------------------------------------------------------------ 项目目录
    private static string FindProjectDir()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        if (HasServer(dir)) return dir;

        string ini = Path.Combine(dir, "launcher.ini");
        if (File.Exists(ini))
        {
            try
            {
                foreach (string raw in File.ReadAllLines(ini))
                {
                    string line = raw.Trim();
                    if (line.StartsWith("dir=", StringComparison.OrdinalIgnoreCase))
                    {
                        string p = line.Substring(4).Trim().Trim('"');
                        if (HasServer(p)) return p;
                    }
                    else if (line.StartsWith("bat=", StringComparison.OrdinalIgnoreCase))
                    {
                        string p = line.Substring(4).Trim().Trim('"');
                        string d = Path.GetDirectoryName(p);
                        if (d != null && HasServer(d)) return d;
                    }
                }
            }
            catch (Exception e) { Log("读 launcher.ini 失败: " + e.Message); }
        }

        // 2) 先在「文档」下试常见的同级目录名（不写死任何绝对路径）
        string docs = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
        foreach (string name in new string[] { "tagcraft", "TagCraft" })
        {
            string guess = Path.Combine(docs, name);
            if (HasServer(guess)) return guess;
        }

        // 3) 在「文档」目录下最多四层内搜索名为 tagcraft 且含 server.py 的文件夹
        return Search(docs, 0, 4);
    }

    private static bool HasServer(string dir)
    {
        try { return dir != null && File.Exists(Path.Combine(dir, "server.py")); }
        catch { return false; }
    }

    private static string Search(string dir, int depth, int maxDepth)
    {
        if (depth > maxDepth) return null;
        try
        {
            string[] subs = Directory.GetDirectories(dir);
            foreach (string sub in subs)
            {
                if (string.Equals(Path.GetFileName(sub), "tagcraft",
                                  StringComparison.OrdinalIgnoreCase) && HasServer(sub))
                    return sub;
            }
            foreach (string sub in subs)
            {
                string r = Search(sub, depth + 1, maxDepth);
                if (r != null) return r;
            }
        }
        catch { }
        return null;
    }

    // ------------------------------------------------------------ 找可用的 Python
    private static List<string> Candidates()
    {
        var list = new List<string>();

        string pathVar = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (string raw in pathVar.Split(';'))
        {
            string d = raw.Trim();
            if (d.Length == 0) continue;
            if (d.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0)
                continue;   // 商店别名，无窗口启动时不可靠
            try
            {
                string p = Path.Combine(d, "python.exe");
                if (File.Exists(p) && !list.Contains(p)) list.Add(p);
            }
            catch { }
        }

        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        foreach (string v in new string[] { "314", "313", "312", "311", "310", "39", "38" })
        {
            string p = Path.Combine(local, @"Programs\Python\Python" + v + @"\python.exe");
            if (File.Exists(p) && !list.Contains(p)) list.Add(p);
        }

        string win = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        if (win.Length > 0)
        {
            string py = Path.Combine(win, "py.exe");
            if (File.Exists(py) && !list.Contains(py)) list.Add(py);
        }

        string up = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string dsh = Path.Combine(up,
            @".dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe");
        if (File.Exists(dsh) && !list.Contains(dsh)) list.Add(dsh);

        return list;
    }

    /// <summary>实际跑一次，确认这个解释器可用。</summary>
    private static bool Works(string python)
    {
        try
        {
            bool isPy = string.Equals(Path.GetFileName(python), "py.exe",
                                      StringComparison.OrdinalIgnoreCase);
            var psi = new ProcessStartInfo(python,
                (isPy ? "-3 " : "") + "-c \"import sys\"");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            using (var p = Process.Start(psi))
            {
                p.StandardOutput.ReadToEnd();
                p.StandardError.ReadToEnd();
                if (!p.WaitForExit(10000))
                {
                    try { p.Kill(); } catch { }
                    return false;
                }
                return p.ExitCode == 0;
            }
        }
        catch (Exception e)
        {
            Log("  候选不可用 " + python + " → " + e.Message);
            return false;
        }
    }

    private static string FindPython(out string diag)
    {
        var tried = new List<string>();
        foreach (string c in Candidates())
        {
            tried.Add(c);
            if (Works(c)) { diag = "选用 " + c + "（已实测可用）"; return c; }
        }
        diag = "没有可用的 Python。候选：" + string.Join(" | ", tried.ToArray());
        return null;
    }

    /// <summary>顺手清掉"来自网络"标记。</summary>
    private static void TryUnblock(string file)
    {
        try
        {
            string zone = file + ":Zone.Identifier";
            if (File.Exists(zone)) File.Delete(zone);
        }
        catch { }
    }

    [STAThread]
    private static void Main()
    {
        Log("──────── 启动 ────────");

        string dir = FindProjectDir();
        Log("项目目录: " + (dir ?? "(未找到)"));
        if (dir == null)
        {
            Log("失败：找不到 server.py");
            MessageBox.Show(
                "没有找到 TagCraft 项目（server.py）。\n\n" +
                "请把本程序放回项目目录（和 server.py 同一个文件夹），\n" +
                "或在本程序旁边放一个 launcher.ini 写上：\n" +
                "    dir=D:\\你的路径\\tagcraft\n\n" +
                "诊断日志：" + LogPath,
                "TagCraft 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        string server = Path.Combine(dir, "server.py");
        string diag;
        string python = FindPython(out diag);
        Log("Python: " + diag);

        try
        {
            if (python != null)
            {
                TryUnblock(server);
                bool isPyLauncher = string.Equals(Path.GetFileName(python), "py.exe",
                                                  StringComparison.OrdinalIgnoreCase);
                string args = (isPyLauncher ? "-3 " : "") + "\"" + server + "\"";
                var psi = new ProcessStartInfo(python, args);
                psi.WorkingDirectory = dir;
                psi.UseShellExecute = false;   // 不走 shell：没有安全警告弹窗
                psi.CreateNoWindow = true;     // 不弹黑框：服务在后台静默运行
                Process p = Process.Start(psi);
                Log("已启动：PID " + p.Id + " → " + python + " " + args);
                return;
            }

            // 没找到可用 Python：退回 start.bat（它会打印"请安装 Python"）
            string bat = Path.Combine(dir, "start.bat");
            if (File.Exists(bat))
            {
                TryUnblock(bat);
                var psi = new ProcessStartInfo("cmd.exe", "/c \"\"" + bat + "\"\"");
                psi.WorkingDirectory = dir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = false;   // 这次要看得见提示
                Process.Start(psi);
                Log("回退：用 start.bat 启动");
                return;
            }

            Log("失败：没有 Python 也没有 start.bat");
            MessageBox.Show("没有找到可用的 Python，也没有找到 start.bat。\n\n诊断日志：" + LogPath,
                "TagCraft 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        catch (Exception ex)
        {
            Log("启动异常: " + ex);
            MessageBox.Show("启动失败：" + ex.Message + "\n\n诊断日志：" + LogPath,
                "TagCraft", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
