# 开源电磁仿真验证

实际求解器：flaport/fdtd 0.3.5，MIT 许可证。配置代码没有自行实现电磁场推进。

结果位于 `结果/验证报告.md`。模型为二维 TMz 线性腔体，不是三维模型，也未验证非线性材料与真实天线。
272 次直接 FDTD 运行中包含 256 个加法输入、减乘除混淆样例、细网格检查与三档空腔基准。
完整时间波形保存于 `结果/receiver_arrays.npz`，代表性波形另有 CSV 和 PNG。

## 第二轮验证

`第二轮结果/` 保存相位控制减法、固定解码扰动测试，以及独立 ahkab 二极管电路的静态乘除测试。
电路模型与二维电磁模型分开求解，不是 GHz 电磁／电路联合仿真。
相位反转由理想配置控制，运算符波探测和相移器本体没有仿真。

```powershell
$env:PYTHONPATH = (Join-Path $env:TEMP 'ewc-openems\fdtd-package') + ';' + (Join-Path $env:TEMP 'ewc-openems\ahkab-package')
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_stage2.py"
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_nonlinear.py"
```

`ahkab` 为 GPL-2.0 开源项目；安装在隔离临时目录。
旧版初始化需要的 SciPy 窗函数名称由脚本恢复别名，器件方程和数值求解器没有修改。
电路示例网表包含二极管模型定义，并经重新解析与独立求解检查。
两种脚本分别生成相位报告和非线性报告；不会覆盖上轮 `结果/` 的数据。

## 第三轮：精度与理论

报告位于 `第三轮结果/精度与理论验证报告.md`，包括18次新的有限宽源二维FDTD、
1004个DC扫描工作点、14次成功的寄生电容瞬态，以及闭式关系和确定性解码误差界。
DC扫描计数不包括瞬态初始化工作点和排错重跑。
本轮有限宽源模型不同于旧点源模型，不能直接把两组场幅度混用。

```powershell
$env:PYTHONPATH = (Join-Path $env:TEMP 'ewc-openems\fdtd-package') + ';' + (Join-Path $env:TEMP 'ewc-openems\ahkab-package')
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_precision_fdtd.py"
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_precision_circuit.py"
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\verify_precision.py"
```

独立环境也可使用下方安装的venv运行这三个脚本。
核验脚本检查归档数据，不重新推进场或电路，也不证明实物准确性。
电路脚本记录欧拉交叉检查失败后仍归档成功的TRAP结果；
固定欧拉遇到上游None/int兼容性错误，自适应欧拉在理想阶跃处步长过小。
因此不能将脚本退出码0或核验PASS解释为欧拉检查通过。
0.2 pF是外加的假设常数电容，ahkab没有内部二极管结电容建模。
完整波形网格差仍约1.05%，复投影时间步差约0.283%，不存在已验证的实物总精度预算。

## 第四轮：有源接收机级联

报告位于 `第四轮结果/级联接口验证报告.md`。
两个数据源分别使用3.0/3.6 GHz，运算符使用4.5 GHz；单个接收波形通过固定线性矩阵分离输入，
测得的幅度驱动ahkab算术电路，四种模式共用一个电流输出和固定增益。
算术在有源接收机内，不是被动腔体；线性解调、电流转换、模式开关与除零保护仍为理想接口。
这是单向级联模型，不是自洽射频/电路联合仿真，也没有验证连续帧速度。

```powershell
$env:PYTHONPATH = (Join-Path $env:TEMP 'ewc-openems\fdtd-package') + ';' + (Join-Path $env:TEMP 'ewc-openems\ahkab-package')
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_cascade.py"
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\verify_cascade.py"
```

归档包括34次直接FDTD、4096个线性基响应重建输入帧、3978个实际DC工作点。
名义1008个有效算术组合全通过，但B增益+0.1%时除法只有135/240通过。
同频负对照中(2,8)与(5,5)的接收波形数值相同，而乘积不同。
不得将第四轮结果解释为原同频纯波四则运算方案已成功。
`verify_cascade.py`验证已有数据并生成结果图；第三轮配置扩展后默认波形回归差为零。

## 第五轮：逐位波逻辑候选

报告位于 `第五轮结果/稳定波逻辑方案与速度能耗预算.md`。
输入按比特编码，通过电磁叠加、理想进位阈值、反相抵消和信号再生构成全加器，
再用逐位加减实现0～15整数的加减乘及商/余数除法。
这不是纯被动计算：阈值、寄存器、控制和波再生需要有源器件。

```powershell
$env:PYTHONPATH = (Join-Path $env:TEMP 'ewc-openems\fdtd-package') + ';' + (Join-Path $env:TEMP 'ewc-openems\ahkab-package')
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_wave_logic.py"
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\verify_wave_logic.py"
```

38次直接FDTD验证两个条件的16个双阶段门状态。
2048项算术/除零测试使用测得门状态的离散事件模型，不是2048次整机FDTD。
名义与εr+1%条件全通过；独立投影增益误差±10%、有界偏置/噪声0.05的原语端点测试也全通过。
器件级阈值、时序、连续帧和真实功率尚未验证。
100 ns/单元、0.2 W等速度能耗参数是工程假设，不是求解器测得结果；不能据此宣称实物零错误或优于CPU。

## 第六轮：有限供电的基带判决再生

报告位于 `第六轮结果/基带判决再生与供电能耗验证报告.md`。
`run_spice_regeneration.py`使用官方ngspice-47求解通用MOS差分对、电流镜、尾偏置管和两级反相器，
包含外加节点电容、输出负载及供电分压器。Python没有自行实现电路求解器。
`run_regeneration.py`保留前期未成功的ahkab完整电路候选，不是有效结果入口。
其中MOS工作点报告数组兼容层不改变器件方程；最小CMOS测试成功不能当成完整候选通过。

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
$spice = Join-Path $env:TEMP 'ewc-openems\ngspice47\Spice64\bin\ngspice_con.exe'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_spice_regeneration.py" --executable $spice
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\verify_spice_regeneration.py"
```

ngspice从官方SourceForge发布包解压到隔离临时目录；失效时重新下载并指定实际程序路径。
288项静态状态和36项边界点全通过；20 ns周期的连续基带判决通过，
20 pF负载压到5 ns周期时只有9/32次采样通过，失败保留在归档中。
200/100/50 ps为自适应求解器的最大时间步；独立核验已检查波形、采样和供电积分。
名义1 pF、320 ns的两个独立通道平均供电约1.852 mW，总供电能量约0.5926 nJ。
它们不是整机功率或每次算术运算能量，不能替换第五轮的整机预算。
器件是通用Level-1模型及假设固定电容，不是实测工艺；输入和阈值源仍理想。
第二通道输入仍来自第五轮已保存的场坐标，没有接入本次进位输出反馈，也未解决RF检波、寄存器或完整算术执行。
核验PASS表示数据一致，不代表5 ns失败工况通过或实物零错误。

## 第七轮：实际进位驱动第二阶段抵消

报告位于 `第七轮结果/实际进位抵消链与采样时序验证报告.md`。
`run_coupled_logic.py`在同一个ngspice瞬态中，让实际MOS进位输出经RC支路驱动第二阶段抵消，
不再给第二阶段提供预先计算的和位输入。它是前向因果级联，不是振荡反馈闭环。
电磁接口仍由既有FDTD标定的理想线性VCVS表示，路径时间常数是假设，不能称连续RF联合仿真。

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
$spice = Join-Path $env:TEMP 'ewc-openems\ngspice47\Spice64\bin\ngspice_con.exe'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_coupled_logic.py" --executable $spice
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\verify_coupled_logic.py"
```

144个完整静态状态全通过。16组连续瞬态各覆盖64种有向状态跳转，归档2080个输出检查。
同时检查周期90%采样点和最后20%的稳定窗口；失败保留。
名义1 pF的20 ns周期通过，10 ns周期只有106/130；20 pF负载的40 ns周期通过，20 ns只有106/130。
指定组合扰动40 ns时单点全部通过，但稳定窗口只有121/130；改为50 ns后130/130，最小末段裕量约0.648 V。
名义40 ns时最长观测建立约13.307 ns，仍存在过渡毛刺，尚未用硬件锁存器消除。
最大步200/100/50 ps及Gear交叉检查名义结果一致，但完整波形差不单调，不能宣称高阶收敛。
独立核验重新检查真值、实际静态抵消关系、采样、稳定窗口、建立时间、积分及断开抵消负对照。
Vdd积分不含理想线性叠加/抵消源和RF接口能量；不是整机速度能耗，也未证明电磁场承担主要计算。
下一步需要保持/时钟、多位进位及真实射频接口验证。

## 第八轮：可靠性与速度代价

`run_reliability.py` 使用同一开源 ngspice 基带网表，扫描8个确定性工况和
10/15/20/30/40/50/60/80 ns周期；覆盖全部64种有向状态跳转。
独立修改两级路径增益和参考电压，区分静态逻辑失败与有限周期内未稳定。
候选周期另用100/50 ps最大步长和Gear法交叉检查。
`verify_reliability.py` 从原始波形重算采样、末段窗口、建立时间和Vdd供电积分。

实际完成64次静态求解、88组瞬态和11,440个输出检查，独立核验通过。
名义、20 pF重负载和指定组合扰动的最低通过网格周期分别为20、40、50 ns。
两组复合增益偏差出现静态错误，80 ns仍不能全部通过；不是降频可以修复的情况。
详细边界及供电能量见`第八轮结果/可靠性边界与可靠运行代价报告.md`。

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_reliability.py" --executable "$env:TEMP\ewc-openems\ngspice47\Spice64\bin\ngspice_con.exe"
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\verify_reliability.py"
```

结果保存在`第八轮结果`，失败不会被筛除。周期网格的最低通过点不是连续频率的
保证上限，测试通过比例也不是实物错误概率。
叠加/抵消仍为理想受控源，不包含真实RF接口、保持寄存器、随机噪声或多位算术执行。
接口功率敏感性表中的0/10/100/1000 mW只是参数假设，不是RF耗电测量。

## 第九轮：独立抵消增益标定

`run_gain_calibration.py`用已知训练信号测量两级路径增量、实际进位高低电平及抵消响应，
通过固定公式确定`cancellation_trim`，计算阶段不按正确答案调整参数。
原电路该参数默认1，默认网表兼容性检查通过。结果独立保存在`第九轮结果`。

重新测试第八轮八个工况和周期网格，另扫描六条路径及完整抵消系数的独立残余误差，
以及冻结增益后的抵消漂移。直流误差包络和四个端点的瞬态测试分别报告；
它们不是整个连续动态误差盒、所有输入历史或实物可靠性的证明。
训练信号采集时间和局部Vdd积分已归档，ADC、调增益器和控制开销仍未建模。
不把单比特结果外推为已经完成的4位串行、并行或数字加法器性能比较。

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
$spice = Join-Path $env:TEMP 'ewc-openems\ngspice47\Spice64\bin\ngspice_con.exe'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_gain_calibration.py" --executable $spice
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\verify_gain_calibration.py"
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\report_gain_calibration.py"
```

报告为`第九轮结果/增益标定修复与可靠运行代价报告.md`。
核验PASS表示原始波形和摘要一致，不表示所有短周期或大残余误差工况通过。

## 第十轮：全独立角点与动态漂移

`run_dynamic_reliability.py`固定第九轮训练参数，测试六条正向路径及完整抵消系数
各自±1%的全部128个符号角点，另加入32个区间内部点。
主序列覆盖64种有向跳转；选定压力角点及中心点增加全部512种连续三状态历史、
快速交替、持续进位和保持序列，并确认候选周期及有界漂移轨迹。
超预算漂移和短周期失败完整保留，不通过计算真值调增益。

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
$spice = Join-Path $env:TEMP 'ewc-openems\ngspice47\Spice64\bin\ngspice_con.exe'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_dynamic_reliability.py" --executable $spice --workers 4
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\verify_dynamic_reliability.py"
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\report_dynamic_reliability.py"
```

结果位于`第十轮结果`，报告为`全角点动态可靠性与漂移边界报告.md`。
`--resume`仅复用配置、来源及代码指纹一致且完整的用例；`--workers 1`可顺序运行。
归档采用`set numdgt=17`；求解器同时间记录另存`runs/*.raw.npz`，全部原始点用于
稳定窗口、建立时间和供电积分，唯一递增轴仅用于插值采样。
独立核验须等八个工况全部完成后执行；报告校验摘要哈希，拒绝旧核验结果与新摘要混用。
全角点主扫描与选定用例交叉检查的覆盖范围不同，不是全部角点与所有历史的笛卡尔积，
也不证明连续动态误差盒或真实硬件100%可靠。本轮不含多位性能对照。

## 第十一轮第一阶段：三维有负载接口提取

使用既有flaport/fdtd推进三维场，添加介质损耗和分布式接收电导。
提取四源四读出的因果响应，以独立有限带宽激励检验重建；
补充5、2.5、2 mm网格检查、时间细化、负载与介质变化及延长窗口。
报告位于`第十一轮结果/三维有负载接口提取与联合仿真前置报告.md`。

```powershell
$env:PYTHONPATH = Join-Path $env:TEMP 'ewc-openems\fdtd-package'
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
python "开源FDTD验证\run_loaded_em_interface.py"
python "开源FDTD验证\refine_loaded_em_interface.py"
python "开源FDTD验证\verify_loaded_em_interface.py"
python "开源FDTD验证\report_loaded_em_interface.py"
```

源与读出仍为归一化体积接口，分布式负载不是50欧姆端口。
本阶段不包含实际射频检测器、一位全加器联合求解或四位性能对照。
归档核验PASS不表示物理端口已验证，也不表示响应已经收敛。

## 在本机重跑

首次运行使用了本机 Python 3.11.9、NumPy 1.26.4、SciPy 1.17.1、Matplotlib 3.10.8。
开源求解器通过清华 PyPI 镜像安装在临时目录，没有修改系统 Python 的依赖。

PowerShell：

```powershell
$env:PYTHONPATH = Join-Path $env:TEMP 'ewc-openems\fdtd-package'
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'ewc-openems\pycache'
python "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_fdtd.py"
```

输出会覆盖本目录下由此脚本生成的结果文件；不修改《构想.md》。

## 临时依赖失效后的复现

使用 Python 3.11，新建独立环境并从国内镜像安装固定版本：

```powershell
$venv = Join-Path $env:LOCALAPPDATA 'EWC-FDTD\venv'
python -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\requirements.txt"
& "$venv\Scripts\python.exe" "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_fdtd.py"
& "$venv\Scripts\python.exe" "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_stage2.py"
& "$venv\Scripts\python.exe" "d:\work\trae_cn\电磁波计算机构想\开源FDTD验证\run_nonlinear.py"
```

不需要激活脚本，也不需要修改 PowerShell 执行策略。

## openEMS 下载记录

openEMS 官方项目：`thliebig/openEMS-Project`，版本 `v0.37.0`。
通过 `ghfast.top` 下载与官方相同的 Windows 发布包，并核对官方 SHA-256：

```text
e4f14fd6e9be7d0c1fd9c244768c21c1c2b62b8aa81f08253ebf6e06040cda5a
```

Python 3.13 便携版通过华为镜像下载，其 `python.exe` 的 Python Software Foundation 签名检查为 Valid；
科学计算依赖通过清华镜像安装到独立目录。
openEMS 的扩展模块被 Windows 应用控制阻止，独立程序也无法启动，没有修改系统安全策略。
所以改用纯 Python 的既有开源求解器完成上述二维验证。

`../openEMS验证/run_openems.py` 只保留未成功执行的三维候选配置，不能将它当成已有仿真结果。

## 解释边界

固定解码只接受接收波形，不接受 A、B；目标算术仅用于结果对照。
噪声图来自接收标量的统计后处理，不是器件物理噪声仿真。
粗细网格波形差异约 13%，所以不据此声称绝对场强、实际硬件精度或整机性能。
不同答案的波形混淆只否定此模型的对应实现，不否定其他几何、编码或非线性方案。
