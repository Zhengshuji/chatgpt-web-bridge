' 隐藏窗口启动 service.ps1（常驻监督），并一直等它。
' 由计划任务调用：wscript.exe service.vbs
'
' 为什么第二个参数是 True（等待）：计划任务在它的动作进程退出时，会回收整棵进程树。
' 如果这里不等待，wscript 立刻退出 → 任务结束 → 监督进程被一起杀掉，
' 表现就是"每 10 分钟弹一次、刚起来又没了"。等它，任务就一直处于 Running，进程树才活着。
Dim fso, sh, dir, script, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
script = dir & "\service.ps1"
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """"
' 0 = 隐藏窗口, True = 等它结束
sh.Run cmd, 0, True
