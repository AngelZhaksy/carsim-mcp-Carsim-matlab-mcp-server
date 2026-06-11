# CarSim MCP 使用手册 + 踩坑记录（carsim-mcp-use.md）

> 面向后续 agent / 自己的实战手册：**怎么用这个 CarSim MCP 改参数、跑工况、读结果**，以及
> **每一个真实踩过的坑**（带现象、根因、修法）。配套：`carsim-matlab.md`（联合仿真细节）。
> 最后更新 2026-06-12。canonical 工程 = `E:\desktop\file\train\dist\carsim-mcp\`（已开源
> github.com/AngelZhaksy/carsim-mcp-Carsim-matlab-mcp-server）；安装目录见末尾。

---

## 0. 一句话

CarSim 的 **GUI 闭源，但数据全是纯文本 parsfile**。这个 MCP 给 agent 17 个工具，让你**不开 GUI、纯代码**
就能：导航数据库 → 改任意参数（悬架/质量/电机…）→ headless 跑求解器 → 读 ERD 结果 / 联合仿真。

---

## 1. 前置条件（不满足必失败）

1. **先开通行证**：跑求解器/GUI 前必须先启动 `E:\Carsim2024\Programs\cslm.exe`（CarSim License Manager）。
   不开会 FlexNet 报错（`-8,523`）。本 MCP 不绕授权，cslm 是官方正常步骤。
2. **MCP 已加载**：改了 MCP 代码后要**重启 Claude Code**（MCP 在会话启动时加载，不热更新）。
3. **建关键字字典（每装机一次）**：`build_keyword_dictionary()` 扫 `DATA\Results\**\*_echo.par` →
   `keywords.json`（本机 ~2042 关键字，给 `get_dataset`/`describe_keyword` 提供释义+单位）。
4. 确认工具链：`carsim_info()` 返回 `ok:true`（各路径 `exists` 为 true）。

---

## 2. 17 个工具速查

| 类 | 工具 | 用途 |
|---|---|---|
| 体检 | `carsim_info` | 路径/版本体检，"是否接好了" |
| 体检 | `launch_gui` | 开 GUI（改参数**不需要**它） |
| 体检 | `list_examples` | 列 CPAR 归档 + Simulink 示例 |
| **导航** | `list_libraries` | 列 GUI 各库（Suspensions/Powertrain/Vehicles/IO_Channels…） |
| **导航** | `browse_library(lib, cat?)` | 列某库/类下的数据集 + 路径 |
| **导航** | `find_dataset(query)` | 全库模糊搜"在哪改 X" |
| **读写** | `get_dataset(path)` | 结构化读：身份+参数（带释义+单位）+表格 |
| **读写** | `set_dataset(path, edits)` | 改参数（自动 .bak / 解只读 / 刷 #Modified） |
| **读写** | `describe_keyword(kw)` | 查关键字释义+单位 |
| **读写** | `build_keyword_dictionary()` | 建本地关键字字典（一次性） |
| **读写** | `read_parsfile`/`write_parsfile` | 底层关键字读/改（上层用 get/set_dataset 更好） |
| **求解** | `run_solver(simfile)` | headless 跑求解器（不碰 MATLAB） |
| **求解** | `read_results(path)` | 解析 ERD(.vs/.vsb) 或 .mat → 各通道 min/max/final |
| **联合** | `generate_simfile` / `scaffold_cosim` / `run_cosim_headless` | 联合仿真（详见 carsim-matlab.md） |

---

## 3. 工作流 A：改参数（最常用）

三步：**搜 → 读（带释义）→ 改**。

```text
find_dataset("sprung mass")          # → Vehicles\Sprung_Mass 各数据集 + 路径
get_dataset(path)                    # → M_SU:{1020,"kg","簧载质量"}, H_CG_SU:{375,"mm",...}
set_dataset(path, {"M_SU":"1500","H_CG_SU":"530"})   # 改值 + 自动 .bak + 解只读
```

**各类参数在哪个库**：

| 改什么 | 库 / 类 | 真实关键字 |
|---|---|---|
| 簧载质量/质心/惯量 | `Vehicles\Sprung_Mass` | `M_SU`(kg) `H_CG_SU`(mm) `IZZ_SU`(kg·m²) `LX_CG_SU` |
| 非簧载质量 | 整车/悬架 | `M_US`（每轴一个，前/后） |
| 悬架（跳动/刚度/运动学/柔度） | `Suspensions\Jounce_Rebound`/`Compliance`/`Kin_*` | 表格如 `F_JNC_STOP_TABLE` |
| 电机/发动机/混动 | `Powertrain\Motor`/`Engine`/`HEV` | `INSTALL_MOTOR`/`INSTALL_ENGINE`/`OPT_HEV` |
| 转向 / 轮胎 | `Steering` / `Tires` | 见 Help\Screens\*.pdf |
| 联合仿真 IO | `IO_Channels` | import/export 通道 |

> 不懂关键字？`describe_keyword("IZZ_SU")` 直接给释义+单位。

---

## 4. 工作流 B：搭一个工况并 headless 跑通（以双移线为例，已实测）

这是 2026-06-12 跑通的真实流程，**照抄能复现**。

### 4.1 找现成工况做底座
```text
find_dataset("double lane change")   # CarSim 自带多个：Procedures / Control\Driver
```
**关键**：不要从零手拼 Run（会撞 parse-order 错误，见坑#3）。用 `DATA\Results\<run>\Run_all.par`——
这是 CarSim 已经"烘焙"好的**合并参数文件**，自包含、parse-order 安全。挑一个车型接近的（看 `M_SU`/`LX_AXLE`）。

### 4.2 复制底座 + 注入参数
```text
# 复制 Run_all.par 到工作目录（解只读），用 set_dataset 注入你的车
set_dataset("<工作目录>\dlc_prd.par", {"M_SU":"2029","IZZ_SU":"3127.6","H_CG_SU":"520","SPEED_TARGET_CONSTANT":"72"})
```
注意：CarSim `M_SU` 是**簧载**质量；总质量 = M_SU + Σ非簧载(`M_US`)。要让总质量=目标，先减掉 unsprung。
`SET_ISPEED_FOR_ID` 会让**初速度跟随** `SPEED_TARGET_CONSTANT`，所以只改这一处即可。

### 4.3 写 simfile（★必须单空格，见坑#2★）
在工作目录写一个名为 **`simfile.sim`** 的文件（单空格分隔！）：
```
SIMFILE

SET_MACRO $(WORK_DIR)$ E:\desktop\file\train\carsim_dlc\
SET_MACRO $(OUT)$ $(WORK_DIR)$out\LastRun
FILEBASE $(OUT)$
INPUT $(WORK_DIR)$dlc_prd.par
INPUTARCHIVE $(OUT)$_all.par
ECHO $(OUT)$_echo.par
FINAL $(OUT)$_end.par
LOGFILE $(OUT)$_log.txt
ERDFILE $(OUT)$.vs
PROGDIR E:\Carsim2024\
DATADIR E:\Carsim2024\DATA\
PRODUCT_ID CarSim
PRODUCT_VER 2024.1
VEHICLE_CODE i_i

END
```
- **不要写 `DLLFILE`**：让 wrapper 经 `PROGDIR` 自己解析 `carsim_64.dll`（显式全路径反而加载失败，见坑#5）。
- `INPUT` 指向你的合并 parsfile；`PROGDIR`/`DATADIR` 指向 CarSim 安装与库。
- `VEHICLE_CODE` 用底座的 `MODEL_LAYOUT`（乘用车多为 `i_i`）。

### 4.4 跑 + 读
```text
run_solver("<工作目录>\simfile.sim")          # returncode 0 即成功；stdout 有 "Termination at ... s"
read_results("<工作目录>\out\LastRun.vs")     # → 各通道 min/max/final
```
实测出来：Vx 71.9→72.0 km/h、Station 0→199m、方向盘±89°、Yaw +13/−6.5°（典型双移线）。

---

## 5. 工作流 C：联合仿真（CarSim ↔ MATLAB/Simulink）

`scaffold_cosim` → MATLAB MCP `run_matlab_file(runner_m)` → `read_results`。详见 **carsim-matlab.md §4**。
要点：MATLAB 执行交给官方 MATLAB MCP；CarSim MCP 只准备文件 + 读结果。

---

## 6. 坑 / Pitfalls（务必读）

| # | 坑 | 现象 | 根因 / 修法 |
|---|---|---|---|
| 1 | **没开 cslm** | FlexNet `-8,523` | 跑前先启 `cslm.exe` 开通行证 |
| 2 | **★ simfile 多空格对齐** | `Unable to load library: ...solvers\ i_i_64.dll`（路径里带空格） | wrapper 解析 PROGDIR/求解器路径不去多余空格 → 畸形路径。**必须单空格分隔**（对齐真实 `DATA\simfile.sim` 格式）。排查这个浪费了很久 |
| 3 | **用了 `Runs\Run_xxx.par`** | parse-ORDER 错误（如 `SET_UNITS_TABLE_ROW ... doesn't exist`） | 改用 `Results\<run>\Run_all.par`（合并、自包含、顺序安全）。新装 DB 已自带 ~574 个 |
| 4 | **cwd 没有 `simfile.sim`** | `Unable to find sim file: simfile.sim` | wrapper **强制** cwd 里有名为 `simfile.sim` 的文件。run_solver 用 `simfile.parent` 作 cwd，所以你的文件就叫 simfile.sim |
| 5 | **显式 `DLLFILE` 全路径** | `Unable to load library: ...carsim_64.dll`（文件明明存在） | 全路径 plain LoadLibrary 找不到 `solvers\` 里的同级依赖（maux/road/tire_64.dll）。**删掉 DLLFILE**，靠 PROGDIR 自解析（wrapper 会正确设置依赖搜索路径） |
| 6 | **★ ERD `.vsb` 通道全错位** | `read_results` 给出 Vx=359km/h、刹车状态=位移 之类乱码 | `.vsb` 开头有 **6 个 float(24字节)头**，旧版 read_results 没跳过 → 整体错位。**已在 2026-06-12 修复**（`header=raw.size%nchan` 跳过）。`.vs` 的 Channels 顺序==列序，跳头后即对齐。⚠️ 这极可能就是早先误判"CarSim 取不出自洽数据"的真因 |
| 7 | **库文件只读** | 写入 PermissionError | CarSim 自带库 `.par` 是只读的。`set_dataset`/`write_parsfile` **已自动解只读**并先写 `.bak`（GUI 保存时也这么做） |
| 8 | **没建关键字字典** | `get_dataset` 没释义、`describe_keyword` 说 unknown | 先跑一次 `build_keyword_dictionary()`（需 `DATA\Results` 里有 echo 文件；没有就先在 GUI 跑任意示例生成） |
| 9 | **改了 MCP 代码不生效** | 工具行为还是旧的 | MCP 会话启动时加载。改 `server.py`/`carsim_core.py` 后**重启 Claude Code**。插件安装在缓存目录（见末尾），改源码后要同步过去 |
| 10 | **自行车模型参数硬套** | kf/kr 找不到对应车辆关键字 | PRD 的 `kf/kr`(轴侧偏刚度) 是 2DOF 抽象，CarSim 里对应**轮胎数据集**，不是单个关键字。能干净映射的是质量/惯量/质心/轴距；侧偏刚度要改轮胎。CarSim 是全多体，不是自行车模型 |
| 11 | **CarSim 不建模电机 FOC** | 想验 dq 电流环/SVPWM 取不到 | CarSim 把电机当**力矩/效率 MAP**，无 PMSM dq/FOC/SVPWM。电驱细节要在 MATLAB/Simulink 做 |
| 12 | **GUI 开不到指定界面** | 想"打开某 input/output 窗口" | `CarSim_64.exe` **无 CLI 打开指定界面**（只到上次 Run Control）。本 MCP 走纯代码改 parsfile，不依赖 GUI |

---

## 7. CarSim 真实结构速记

- 工作库：`E:\Carsim2024\DATA`（`Suspensions`/`Powertrain`/`Vehicles`/`Procedures`/`Control`/`Roads`/`IO_Channels`…）。
- 每个 `.par` 头部带 GUI 面包屑：`#FullDataName 库`数据集`类别`，末尾有 `#Library/#DataSet/#Category/#FileID/#Modified`。
- Run：`DATA\Runs\Run_<guid>.par`（链接式，**勿直接跑**）；`DATA\Results\Run_<guid>\Run_all.par`（合并式，**可跑**）。
- ERD 结果：`.vs`(JSON 头) + `.vsb`(float32 二进制，开头 6-float 头 + 行主序帧)。
- 关键字字典权威源：`DATA\Results\**\*_echo.par`（行如 `M_SU 1370 ; kg ! Mass of unladen sprung mass`）。
- 手册：`Help\Manuals\vs_commands.pdf`(关键字)、`VS_Math_Models.pdf`(变量+单位)、`Help\Screens\*.pdf`(逐界面)。

---

## 8. 安装 / 位置

- 源码（已开源仓库根）：`E:\desktop\file\train\dist\carsim-mcp\`（`server.py`/`carsim_core.py`/`templates/`/`docs/`）。
- 插件运行目录（live）：`C:\Users\mochasu\.claude\plugins\cache\carsim-mcp\carsim-mcp\1.0.0\`
  —— `keywords.json` 在这里生成；**改源码后要同步到这并重启**。
- GitHub：<https://github.com/AngelZhaksy/carsim-mcp-Carsim-matlab-mcp-server>
  （别人装：`/plugin marketplace add AngelZhaksy/carsim-mcp-Carsim-matlab-mcp-server` → `/plugin install carsim-mcp@carsim-mcp` → `pip install -r requirements.txt`）。
- `keywords.json` 含 CarSim 专有文档文字，已 `.gitignore`，每台机器本地 `build_keyword_dictionary()` 生成，不进仓库。

---

## 9. 一个完整可复现实例

`E:\desktop\file\train\carsim_dlc\`（2026-06-12 实测）：
- `dlc_prd.par` —— 注入 PRD 整车的双移线合并参数（M_SU 2029 / IZZ_SU 3127.6 / H_CG_SU 520 / 72km/h）
- `simfile.sim` —— 单空格 standalone headless simfile（可作模板）
- `out\LastRun.vs` + `.vsb` —— ERD 结果（172 通道 / 401 采样 / 10s）
- `dlc_result.png` —— 轨迹/方向盘/横摆/车速四联图
