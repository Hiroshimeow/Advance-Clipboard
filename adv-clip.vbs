Set shell = CreateObject("WScript.Shell")
shell.Run """" & Replace(WScript.ScriptFullName, ".vbs", ".bat") & """", 0, False
