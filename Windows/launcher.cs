using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Launcher
{
    private const int MaxLogBytes = 262144;
    private const int TailLines = 15;

    private static void AppendLog(string logPath, object gate, Queue<string> tail, string prefix, string line)
    {
        if (line == null) return;
        string entry = string.Format("[{0:yyyy-MM-dd HH:mm:ss}] {1}{2}", DateTime.Now, prefix, line);
        lock (gate)
        {
            tail.Enqueue(entry);
            while (tail.Count > TailLines) tail.Dequeue();
            try
            {
                if (File.Exists(logPath) && new FileInfo(logPath).Length > MaxLogBytes)
                {
                    File.WriteAllText(logPath, string.Join(Environment.NewLine, tail) + Environment.NewLine, Encoding.UTF8);
                }
                else
                {
                    File.AppendAllText(logPath, entry + Environment.NewLine, Encoding.UTF8);
                }
            }
            catch
            {
            }
        }
    }

    [STAThread]
    private static void Main()
    {
        string appDirectory = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Windows");
        string pythonExe = Path.Combine(appDirectory, ".venv", "Scripts", "python.exe");
        string pythonwExe = Path.Combine(appDirectory, ".venv", "Scripts", "pythonw.exe");
        string python = File.Exists(pythonExe) ? pythonExe : pythonwExe;
        string script = Path.Combine(appDirectory, "app.py");
        string dataDirectory = Path.Combine(appDirectory, "data");
        string logPath = Path.Combine(dataDirectory, "launcher.log");

        try
        {
            if (!File.Exists(python) || !File.Exists(script))
            {
                throw new FileNotFoundException("找不到工坊程序或 Python 环境。请将启动器留在优化版目录，与 Windows 文件夹放在一起。");
            }

            Directory.CreateDirectory(dataDirectory);
            object gate = new object();
            var tail = new Queue<string>();

            var start = new ProcessStartInfo(python, "app.py")
            {
                WorkingDirectory = appDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };

            using (Process process = new Process { StartInfo = start })
            {
                process.OutputDataReceived += (sender, args) => AppendLog(logPath, gate, tail, "", args.Data);
                process.ErrorDataReceived += (sender, args) => AppendLog(logPath, gate, tail, "[ERR] ", args.Data);
                if (!process.Start())
                {
                    throw new InvalidOperationException("无法启动 Python 后台进程。");
                }
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();

                if (process.WaitForExit(2500))
                {
                    process.WaitForExit();
                    if (process.ExitCode != 0)
                    {
                        string recent;
                        lock (gate)
                        {
                            recent = tail.Count > 0 ? string.Join(Environment.NewLine, tail) : "（无输出，详见日志文件）";
                        }
                        MessageBox.Show(
                            string.Format("工坊后台意外退出（退出码 {0}）。\n\n最近日志：\n{1}\n\n完整日志：{2}",
                                process.ExitCode, recent, logPath),
                            "球员工坊启动失败",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Error
                        );
                    }
                    return;
                }

                // Keep draining redirected streams so the child never hits a broken pipe.
                process.WaitForExit();
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "球员工坊启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
