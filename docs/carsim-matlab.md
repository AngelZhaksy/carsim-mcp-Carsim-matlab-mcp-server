# CarSim ↔ MATLAB 联合仿真：CarSim MCP 使用指南（给后续 agent）

本文教你（后续接手的 agent）如何用 **CarSim MCP** 驱动 CarSim 2024 做参数编辑，
并与 **MATLAB / Simulink** 做联合仿真（同时支持 `.m` 文件和 `.slx` 模型）。

MCP 工程位于 `E:\desktop\file\train\carsim-mcp\`（含 `DESIGN.md` / `PLAN.md` / `README.md`）。

---

## 0. 一句话职责划分（最重要）

| 工具 | 负责 |
|---|---|
| **CarSim MCP**（`mcp__carsim__*`，本工程新建） | CarSim 域：路径探测、改参数（parsfile）、headless 跑求解器、生成 simfile、搭联合仿真脚手架、解析结果 |
| **MATLAB MCP**（官方，已装） | 所有 MATLAB 执行/编译：`run_matlab_file`、`evaluate_matlab_code`，复用常驻可见的 MATLAB 会话 |

两者通过**文件系统**交接（simfile / .slx / .m / results.mat）。**由你（agent）做编排**：
CarSim MCP 准备 → MATLAB MCP 执行 → CarSim MCP 读结果。

> 为什么不让 CarSim MCP 自己起 MATLAB？因为官方 MATLAB MCP 已经管着一个热的、可见的
> MATLAB 会话，复用它能省去每次 20–30s 冷启动、并让用户在桌面看到曲线/动画。
> CarSim MCP 仅保留一个**可选兜底** `run_cosim_headless`（`matlab -batch`），用于无人值守批量跑。

---

## 1. 前置条件

1. **CarSim 必须有合法 license,且需先开启通行证。** 本机为需许可证的 CarSim,
   **每次打开 CarSim / 跑求解器之前,必须先启动 `E:\Carsim2024\Programs\cslm.exe`
   (CarSim License Manager) 开启通行证**,否则 CarSim 会报 FlexNet 错误(如 `-8,523`)。
   开启后再调用 `launch_gui` / `run_solver` / 联合仿真类工具。
   （本 MCP 不绕过、不修改、不模拟授权;`cslm.exe` 是官方授权管理器,属正常步骤。）
2. **MCP 已注册**在 `C:\Users\<用户>\.claude.json` 的 `mcpServers.carsim`。
   MCP 在**会话启动时加载**——若 `mcp__carsim__*` 工具不可见，请重启 Claude Code / 重载 MCP。
3. **MATLAB MCP 在线**（`mcp__matlab__run_matlab_file` 等可用）。

确认工具链是否就绪：调用 `mcp__carsim__carsim_info`，确认返回 `ok: true`，
且 `vs_sf_mex`、`matlab_exe`、`carsim_db` 的 `exists` 均为 true。

---

## 2. 工具清单（CarSim MCP，共 10 个）

| 工具 | 作用 | 关键返回 |
|---|---|---|
| `carsim_info()` | 解析并体检所有路径/版本，"是否接好了"的探针 | 各路径 `{path,exists}` + 顶层 `ok` |
| `launch_gui(dataset?)` | 启动 CarSim GUI（非阻塞），可选打开数据集 | `pid` |
| `list_examples()` | 列出自带 CPAR 归档 + Simulink 示例模型 | `cpar_archives`, `simulink_models` |
| `read_parsfile(path)` | 解析 parsfile → 关键字字典 | `keywords{KW:[值...]}`, `raw_lines` |
| `write_parsfile(path, edits, mode="set")` | 改参数/IO/数据集链接，自动 `.bak` 备份 | `changed`, `added`, `backup` |
| `run_solver(simfile, timeout=120)` | 纯 CarSim headless 求解（不碰 MATLAB） | `returncode`, `declared_outputs`, `output_dir` |
| `generate_simfile(out_path, run_parsfile?, template_simfile)` | 从 GUI 生成的 simfile 模板派生新 simfile | `simfile`, `parsfile` |
| `scaffold_cosim(out_dir, simfile, kind, model?, controller_call?, stop_time)` | 生成可直接跑的联合仿真包（`.slx` 或 `.m` 驱动 + `run_cosim.m`） | `runner_m`, `model`, `results_path` |
| `read_results(path, max_channels=50)` | 解析 `.mat`/`.erd` → 各通道 `{len,min,max,final}` | `source`, `n`, `channels` |
| `run_cosim_headless(runner_m, timeout=600)` | **可选兜底**：`matlab -batch` 跑驱动脚本 | `cosim_ok`, `results_path` |

---

## 3. CarSim 在本机的真实结构（理解后改参数才不会错）

- **工作数据库**：`E:\Carsim2024\DATA`（CarSim "Select Recent Database" 指向它）。
  - `DATA\Runs\Run_<guid>.par`：每个 Run（工况）的控制 parsfile。
  - `DATA\Results\Run_<guid>\`：求解输出（`LastRun.vs` = ERD 结果、`*_log.txt`、`Run_all.par` 合并参数）。
  - `DATA\simfile.sim`：求解器/`vs_sf` 读取的顶层 simfile。
- **simfile 结构**（`DATA\simfile.sim` 实例，联合仿真关键字已标注）：
  ```
  SIMFILE
  FILEBASE   Results\...\LastRun
  INPUT      Results\<run>\Run_all.par      ← 引用的合并参数文件
  ECHO/FINAL/LOGFILE/ERDFILE ...            ← 各类输出
  PROGDIR    E:\Carsim2024\
  DATADIR    E:\Carsim2024\DATA\
  VEHICLE_CODE i_i
  EXT_MODEL_STEP 0.0005                     ← 联合仿真外部模型步长
  PORTS_IMP  0                              ← 从 Simulink 导入 CarSim 的通道数
  PORTS_EXP  0                              ← 从 CarSim 导出到 Simulink 的通道数
  DLLFILE    E:\Carsim2024\Programs\solvers\carsim_64.dll
  END
  ```
  > 联合仿真时 `PORTS_IMP`/`PORTS_EXP` > 0，且 Simulink 模型里的 **CarSim S-Function
  > (`vs_sf`)** 通过 `simfile` 变量读取这个 simfile。`simfile` 由 CarSim GUI
  > 在选择"Models: Simulink"并点 **Generate Files for this Run** 时写出。

---

## 4. 标准联合仿真配方（你来编排）

### 4.1 用 `.slx`（Simulink 模型，内含 CarSim S-Function）

```text
# 1) CarSim 侧：生成可跑的联合仿真包（默认拷贝自带 example.mdl 作为模型）
res = mcp__carsim__scaffold_cosim(
        out_dir   = "E:\\desktop\\file\\train\\cosim_run1",
        simfile   = "E:\\Carsim2024\\DATA\\simfile.sim",   # 或 generate_simfile 产出的
        kind      = "slx",
        model     = "E:\\Carsim2024\\DATA\\Extensions\\Simulink\\example.mdl",  # 可换成你的 .slx
        stop_time = "5")
# res.runner_m -> ...\cosim_run1\run_cosim.m ; res.results_path -> ...\results.mat

# 2) MATLAB 侧（官方 MATLAB MCP，复用热会话）执行驱动脚本
mcp__matlab__run_matlab_file(script_path = res.runner_m)   # 期望输出含 COSIM_OK

# 3) CarSim 侧：读结果
#    full vehicle channels 在 CarSim ERD（.vs，690+ 通道）里，read_results 直接解析:
mcp__carsim__read_results(path =
    "E:\\Carsim2024\\DATA\\Results\\Run_<guid>\\LastRun.vs")  # -> 各通道 min/max/final
#    （res.results_path 那个 .mat 存的是 Simulink SimulationOutput，通常只含 tout；
#      要全部车辆通道就读上面的 .vs ERD。）
```

> **已实测通过**（2026-06-09，External Steer 示例）：co-sim 跑到 6.31 s 无报错，
> `read_results` 解析出 **690 通道 / 253 采样**(Time 0→6.3s, step 0.025)，
> 含 AAz、AA_Y 等真实车辆动力学量。

`run_cosim.m`（由模板生成）做的事：把 `Programs\solvers\Matlab`（含 `vs_sf.mexw64`）
加入路径 → 设置 `simfile` 变量 → `sim(model,'StopTime',...)` → 存 `results.mat` → 打印 `COSIM_OK`。

### 4.2 用 `.m`（你自己的控制器，例如 SBW 双电机/2DOF 算法）

```text
res = mcp__carsim__scaffold_cosim(
        out_dir         = "E:\\desktop\\file\\train\\cosim_sbw",
        simfile         = "E:\\Carsim2024\\DATA\\simfile.sim",
        kind            = "mfile",
        controller_call = "results = sbw_controller(simfile, stop_time);",  # 必须给 results 赋值
        stop_time       = "5")
mcp__matlab__run_matlab_file(script_path = res.runner_m)
mcp__carsim__read_results(path = "<run 目录>\\LastRun.vs")  # ERD 全通道
```

> `controller_call` 是一段 MATLAB 语句，必须产生变量 `results`。可在其中调用 CarSim 的
> 分体式 S-Function（`vs_ctl`/`vs_dyn`，位于 `Programs\solvers\Matlab`）把控制器嵌进动力学，
> 或在 `sim()` 跑完后从工作区取信号。SBW 课题可把转向控制器写成 `sbw_controller.m` 放进 `out_dir`。

### 4.3 兜底（无人值守批量）

```text
mcp__carsim__run_cosim_headless(runner_m = res.runner_m)   # 内部 matlab -batch，cosim_ok=true 即成功
```

---

## 5. 改参数：车辆参数 / 悬架 / 输入输出

`write_parsfile` 按关键字改值（改第一处出现，缺失则追加），自动写 `.bak` 备份。

```text
# 读，看有哪些关键字
mcp__carsim__read_parsfile(path = "E:\\Carsim2024\\DATA\\Results\\Run_<guid>\\Run_all.par")

# 改车辆质量、设置联合仿真导入/导出端口数
mcp__carsim__write_parsfile(
    path  = "...\\Run_all.par",
    edits = { "M_S": "1700", "PORTS_IMP": "4", "PORTS_EXP": "8" })
```

- **车辆参数**：如 `M_S`（簧载质量）等数值类关键字，直接改值即可。
- **输入/输出（联合仿真 IO）**：`PORTS_IMP` / `PORTS_EXP`（端口数）、`EXT_MODEL_STEP`（外部步长）。
  在 simfile 或合并 parsfile 中调整，需与 Simulink 模型的 import/export 端口对应。
- **悬架 / 转向等"选模型"**：CarSim 里这些是**数据集链接**（log 里 `Used Dataset: Suspension: ... { Strut } ...`）。
  - 可做：把链接指向**已有**的另一数据集（改 parsfile 里对应的链接行/路径）。
  - 不可做：从零**新建**一个悬架/整车数据集——这属于 GUI 数据库操作，用 `launch_gui` 在
    CarSim GUI 里建好后，再用 MCP 引用。

---

## 6. 纯 CarSim 求解（不联合仿真）

```text
mcp__carsim__run_solver(simfile = "E:\\Carsim2024\\DATA\\simfile.sim")
# returncode==0 即成功；输出在 declared_outputs（ERDFILE=.vs 结果、LOGFILE 等）
mcp__carsim__read_results(path = "<上面的 .vs 同名结果或导出的 .mat>")
```

> CarSim 2024 ERD = JSON 头(`.vs`) + float32 数据(`.vsb`)。`read_results` 已能直接
> 解析:返回 `n`(通道数)、`n_samples`、各通道 `{len,min,max,final}`。直接传 `LastRun.vs` 即可。

---

## 7. 常见坑 / 注意事项

- **license 是第一道门。** 没有合法授权，`run_solver` 和联合仿真都会失败（FlexNet 报错）。本 MCP 不绕授权。
- **改 MCP 配置后要重启会话**，`mcp__carsim__*` 才会出现。
- **改 parsfile 前先 `read_parsfile`** 看清关键字；改完有 `.bak` 可回滚。
- **simfile 模板**：`generate_simfile` 需要一个 GUI 生成的 `simfile.sim` 作模板
  （本机现成模板：`E:\Carsim2024\DATA\simfile.sim`）。
- **`.slx` 模型必须含 CarSim S-Function（`vs_sf`）块**，且其 simfile 来源为 `simfile` 变量
  （CarSim 自带示例模型即此默认接线，见 `DATA\Extensions\Simulink\`）。
- **SBW 课题**：可参考 `DATA\Extensions\Simulink\ext_steer_*.mdl`、`Yaw_*` 等转向示例做起点。
- **改了 MCP 代码必须重启 Claude Code**：MCP 服务在会话启动时加载,改 `server.py`/`carsim_core.py`
  后旧进程不会热更新,需重启或重载 MCP 才生效。
- **若某个工具调用一直转圈不返回**（曾在 `read_results` 上出现）：很可能是新加的工具在**函数内部**
  懒加载了 `numpy/scipy` 等原生库——FastMCP 在工作线程跑同步工具,首次在非主线程 import 原生库会在
  Windows 上**死锁**。修法:把这类库放到**模块顶层** import。详见 `carsim-mcp/README.md` 的"故障排查"。
  （此问题已于 2026-06-09 修复并验证。）

---

## 8. 工程自检命令（无需 license，验证 MCP 本体）

```powershell
cd E:\desktop\file\train\carsim-mcp
.\.venv\Scripts\python.exe smoke_test.py          # 纯逻辑：路径/parsfile/示例/脚手架
.\.venv\Scripts\python.exe mcp_handshake_test.py  # 完整 MCP stdio 握手 + carsim_info
```
两个脚本都应打印通过；`carsim_info.ok == True` 表示工具链路径就绪。
（联合仿真的端到端验证另需合法 CarSim license。）
