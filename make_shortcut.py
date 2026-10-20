import os                                   # paths and file checks
import subprocess                           # run PowerShell
import sys                                  # sys.executable = path of the python you are using

folder = os.path.dirname(os.path.abspath(__file__))   # the folder this script lives in
app = os.path.join(folder, "app.py")                   # the app we want to launch

# pythonw.exe is python without the black console window; it sits next to python.exe
pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.exists(pythonw):
    raise SystemExit(f"Could not find pythonw.exe at {pythonw}")

# PowerShell can create .lnk shortcut files; this text is the PowerShell program
script = f"""
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'File Toolkit.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$s.TargetPath = '{pythonw}'
$s.Arguments = '"{app}"'
$s.WorkingDirectory = '{folder}'
$s.Description = 'File Toolkit'
$s.Save()
Write-Output "Shortcut saved to: $link"
"""

result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                        capture_output=True, text=True)
print(result.stdout.strip() or result.stderr.strip())