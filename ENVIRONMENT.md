# Python 环境管理

本项目使用项目内虚拟环境，路径固定为：

`D:\超声光栅实验辅助平台\.venv314`

## 初始化环境

```powershell
cd D:\超声光栅实验辅助平台
.\setup_env.ps1
```

## 运行客户端

```powershell
cd D:\超声光栅实验辅助平台
.\run.ps1
```

也可以直接双击或运行：

```powershell
cd D:\超声光栅实验辅助平台
.\run.bat
```

## 直接使用解释器

```powershell
D:\超声光栅实验辅助平台\.venv314\Scripts\python.exe main.py
```

## 说明

- `.venv314` 是项目专用环境，不提交。
- `requirements.txt` 是实际安装来源。
- `environment.yml` 仅保留为环境摘要。
- 若要清理 Python 缓存，只清理项目源码下的 `__pycache__`，不要删除 `.venv314` 内部文件。
