@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem ======================================================================
rem 文档解析智能体 - 启动菜单
rem
rem 本脚本不含任何硬编码绝对路径：
rem   * 先记住调用者的当前目录（ORIGIN）
rem   * 再向上查找含 src\main.py 的目录作为项目根（ROOT）
rem   * 之后所有路径一律相对 ROOT，脚本可以被拷贝到任意盘符/任意层级
rem ======================================================================

set "ORIGIN=%CD%"
set "ROOT="
call :find_root "%~dp0"
if not defined ROOT call :find_root "%CD%"

if not defined ROOT (
    echo.
    echo [错误] 未能在当前目录及其上级找到项目根目录。
    echo        判据是存在 src\main.py。
    echo.
    echo        当前目录：%ORIGIN%
    echo        请把本脚本放在项目根目录（与 src\ 、examples\ 同级）后再运行。
    echo.
    pause
    exit /b 1
)

rem 所有后续路径都相对项目根，避免中文/空格路径带来的引号问题
cd /d "%ROOT%"

rem 优先使用项目虚拟环境；没有虚拟环境时使用系统 Python。
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
%PY% --version >nul 2>&1 || (
    echo.
    echo [错误] 在 PATH 中找不到 python。
    echo        请先安装 Python 3.10+ 并勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

rem ---- 首次启动依赖检测：缺依赖时询问并自动安装；已就绪则秒级通过 ----
%PY% src\bootstrap.py
if errorlevel 1 (
    echo.
    echo [错误] 依赖检测未通过，请按上方提示处理后重试。
    pause
    exit /b 1
)

title 文档解析智能体 - 启动菜单

:menu
cls
echo ======================================================================
echo   文档解析智能体 . 论文阅读助手          南京邮电大学 2026 级新生赛
echo ======================================================================
echo   项目根目录：%ROOT%
echo ======================================================================
echo.
echo   [1]  打开示例看板          （直接看效果，不需要任何配置）
echo   [2]  离线演示一篇论文      （不调用 API，零费用）
echo   [3]  分析我自己的论文      （需要先在 .env 里配置 API Key）
echo   [4]  启动网页上传界面      （浏览器里拖拽 PDF）
echo   [5]  环境自检              （检查依赖与素材是否齐全）
echo   [6]  运行全部自测          （86 项单元 + 37 项前端 + 冒烟）
echo   [7]  创建 .env 配置文件    （填写 API Key 用）
echo.
echo   [0]  退出
echo.
set /p CHOICE=  请输入序号并回车: 

if "%CHOICE%"=="1" goto open_demo
if "%CHOICE%"=="2" goto mock
if "%CHOICE%"=="3" goto real
if "%CHOICE%"=="4" goto web
if "%CHOICE%"=="5" goto check
if "%CHOICE%"=="6" goto tests
if "%CHOICE%"=="7" goto makeenv
if "%CHOICE%"=="0" exit /b 0
goto menu

rem ----------------------------------------------------------------------
:open_demo
cls
echo 正在打开示例看板（无需联网，断网也能看）...
echo.
if exist "outputs\sample_en_two_column.dashboard.html" (
    start "" "outputs\sample_en_two_column.dashboard.html"
    echo 已用默认浏览器打开英文样例看板。
    echo 中文样例：outputs\sample_zh_single_column.dashboard.html
) else (
    echo [提示] 还没有生成示例看板。
    echo        请先回到菜单选择 [2] 离线演示，生成一次产物。
)
echo.
pause
goto menu

rem ----------------------------------------------------------------------
:mock
cls
echo 离线演示模式：不调用任何大模型 API，零费用。
echo.
echo 可用的示例论文：
echo   1. sample_en_two_column.pdf    英文 6 页双栏（推荐）
echo   2. sample_zh_single_column.pdf 中文 4 页单栏
echo   3. sample_long_outline.pdf     24 页长文档
echo   4. sample_scan_like.pdf        近扫描件（演示降级不崩溃）
echo.
set /p WHICH=  选择论文序号 [1-4]，或直接把 PDF 拖到这里再回车: 

set "PDF="
if "%WHICH%"=="1" set "PDF=examples\sample_en_two_column.pdf"
if "%WHICH%"=="2" set "PDF=examples\sample_zh_single_column.pdf"
if "%WHICH%"=="3" set "PDF=examples\sample_long_outline.pdf"
if "%WHICH%"=="4" set "PDF=examples\sample_scan_like.pdf"
if not defined PDF set "PDF=%WHICH%"
rem 拖拽进来的路径可能带引号，去掉它们
set "PDF=%PDF:"=%"

call :resolve_pdf
if not defined PDF goto menu

echo.
echo 正在处理：%PDF%
echo ----------------------------------------------------------------------
%PY% src\main.py "%PDF%" --mock
set "RC=%ERRORLEVEL%"
echo ----------------------------------------------------------------------
echo.
for %%F in ("%PDF%") do set "OUTNAME=%%~nF"
if %RC%==0 (
    if exist "outputs\!OUTNAME!.dashboard.html" (
        set /p OPEN=  是否打开生成的看板？[Y/N]: 
        if /i "!OPEN!"=="Y" start "" "outputs\!OUTNAME!.dashboard.html"
    ) else (
        echo [提示] 运行成功，但未找到 outputs\!OUTNAME!.dashboard.html
    )
) else (
    echo [提示] 运行未成功（退出码 %RC%），请查看上方错误信息。
)
set "PDF="
set "OUTNAME="
echo.
pause
goto menu

rem ----------------------------------------------------------------------
:real
cls
if not exist ".env" (
    echo [错误] 还没有配置文件 .env
    echo.
    echo 请先回到菜单选择 [7] 创建 .env，并填写你的 API Key。
    echo.
    pause
    goto menu
)
echo 分析模式：将真实调用大模型 API（会计费）。
echo.
set /p PDF=  把 PDF 拖到这里再回车（或输入路径）: 
set "PDF=%PDF:"=%"

call :resolve_pdf
if not defined PDF goto menu

echo.
echo 正在处理：%PDF%
echo ----------------------------------------------------------------------
%PY% src\main.py "%PDF%"
set "RC=%ERRORLEVEL%"
echo ----------------------------------------------------------------------
echo.
for %%F in ("%PDF%") do set "OUTNAME=%%~nF"
if %RC%==0 (
    if exist "outputs\!OUTNAME!.dashboard.html" (
        set /p OPEN=  是否打开生成的看板？[Y/N]: 
        if /i "!OPEN!"=="Y" start "" "outputs\!OUTNAME!.dashboard.html"
    )
) else (
    echo [提示] 运行未成功（退出码 %RC%），请查看上方错误信息。
)
set "OUTNAME="
echo.
pause
goto menu

rem ----------------------------------------------------------------------
:web
cls
echo 正在启动网页上传界面...
echo 浏览器会自动打开 http://127.0.0.1:8000
echo 停止服务请在本窗口按 Ctrl+C
echo ----------------------------------------------------------------------
%PY% src\main.py --web
echo ----------------------------------------------------------------------
pause
goto menu

rem ----------------------------------------------------------------------
:check
cls
%PY% src\main.py --check
echo.
pause
goto menu

rem ----------------------------------------------------------------------
:tests
cls
%PY% tests\run_all_tests.py
echo.
pause
goto menu

rem ----------------------------------------------------------------------
:makeenv
cls
if exist ".env" (
    echo [提示] .env 已存在，将用 .env.example 的内容覆盖它。
    set /p OK=  确定要覆盖吗？[Y/N]: 
    if /i not "%OK%"=="Y" goto menu
)
if not exist ".env.example" (
    echo [错误] 找不到 .env.example，无法创建配置文件。
    echo.
    pause
    goto menu
)
copy /y ".env.example" ".env" >nul
echo 已创建 .env
echo.
echo 接下来请用记事本打开 .env，至少填写这三行：
echo.
echo     LLM_API_KEY=你的真实密钥
echo     LLM_BASE_URL=https://api.deepseek.com/v1
echo     LLM_MODEL=deepseek-chat
echo.
echo 注意：.env 已在 .gitignore 中排除，切勿把真实密钥提交到仓库。
echo.
set /p OPENENV=  现在用记事本打开 .env 吗？[Y/N]: 
if /i "%OPENENV%"=="Y" start "" notepad ".env"
echo.
pause
goto menu

rem ======================================================================
rem 子过程：从当前目录向上查找含 src\main.py 的项目根
rem 参数：%1 = 起始目录
rem 结果：写入 ROOT（找不到则保持未定义）
rem ======================================================================
:find_root
set "PROBE=%~1"
:find_root_loop
if exist "%PROBE%\src\main.py" (
    set "ROOT=%PROBE%"
    goto :eof
)
for %%P in ("%PROBE%\..") do set "PARENT=%%~fP"
if /i "%PARENT%"=="%PROBE%" goto :eof
set "PROBE=%PARENT%"
goto find_root_loop

rem ======================================================================
rem 子过程：把用户给的 PDF 路径解析成可用路径
rem   * 已经是有效路径（相对项目根或绝对路径）→ 原样保留
rem   * 否则尝试相对"启动脚本时的目录"解析，方便用户按自己的习惯输入
rem   * 都找不到 → 提示并清空 PDF，由调用方返回菜单
rem ======================================================================
:resolve_pdf
if not defined PDF goto :eof
if exist "%PDF%" goto :eof
if exist "%ORIGIN%\%PDF%" (
    set "PDF=%ORIGIN%\%PDF%"
    goto :eof
)
echo.
echo [错误] 找不到文件：%PDF%
echo        已尝试：项目根目录、以及 %ORIGIN%
echo.
pause
set "PDF="
goto :eof
