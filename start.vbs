' start.vbs - Silent launcher for 漫画下载器 (runs start.bat with no console window)
Set WshShell = CreateObject("WScript.Shell")
Set Fso = CreateObject("Scripting.FileSystemObject")
WshShell.CurrentDirectory = Fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "start.bat", 0, False
