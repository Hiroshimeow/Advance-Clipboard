Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = """" & currentDir & "\main.bat" & """"

WshShell.Run "cmd /k " & batPath, 0, False