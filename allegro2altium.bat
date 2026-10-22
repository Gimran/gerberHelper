@echo off
chcp 866 >nul
setlocal
cd /d "%~dp0"

rem -- ищем Python: PATH, затем лаунчер py, затем обычное место установки
set "PY="
where python.exe >nul 2>&1 && set "PY=python.exe"
if not defined PY (where py.exe >nul 2>&1 && set "PY=py.exe")
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if not defined PY goto nopython

rem -- без customtkinter окно не откроется, а pythonw промолчит: проверяем заранее
"%PY%" -c "import customtkinter" >nul 2>&1
if errorlevel 1 goto nodep

rem -- pythonw вместо python, чтобы не висела чёрная консоль
set "PYW=%PY:python.exe=pythonw.exe%"
set "PYW=%PYW:py.exe=pyw.exe%"
start "" "%PYW%" "%~dp0gui.py"
exit /b 0

:nopython
echo.
echo   Python не найден.
echo   Поставьте его с python.org, при установке отметьте
echo   "Add python.exe to PATH".
echo.
pause
exit /b 1

:nodep
echo.
echo   Не хватает библиотеки customtkinter, без неё окно не откроется.
echo   Выполните:
echo.
echo       "%PY%" -m pip install customtkinter
echo.
pause
exit /b 1
