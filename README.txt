Script-Editor - 視覺化腳本組合工具（匯出免依賴選單式 .bat）
==========================================================

這是什麼
--------
把常用的factory/維運操作做成「區塊」（Block），用滑鼠拖曳組合多個
「任務」（Task），一鍵匯出單一 .bat 腳本。匯出的腳本「免安裝、免
依賴」（純 cmd + Windows 內建工具），拷到工廠機／待測機就能跑：

  * 主選單列出所有任務，可選單選執行，或 [A] 全部執行（可關）；
    任務 ≤9 時用 choice 一鍵選（按鍵即選、免 Enter），風格仿
    原版 script_tools.bat
  * 每個步驟的參數可「製作時填死」或「執行時詢問」
  * 內建通用、Windows 資訊、驅動/裝置、重開機應力循環等區塊；需要特定
    工具包的區塊放在自己的資料夾，或用「區塊包」跟同事分享
  * 支援自訂區塊（單行指令或 raw 批次行）
  * 步驟可設定：可接受 exit code、失敗停止/跳過/詢問、重複執行
    （次數或時間）、執行前確認、成功後再確認（選否就不跑後續步驟）
  * 任務可設定：分組標題、自訂選單按鍵（如 [M]）、執行前檢查必要檔案、
    找不到工具包時只略過相關步驟；專案可設主選單上方的唯讀狀態列與
    結束時的每步結果清單
  * 參數可轉大寫、限制長度（如 7 碼 Service Tag）

匯出腳本範例
------------
  examples/stress.json   RebootStress：WarmBoot/ColdBoot 應力循環
                         （仿 VKEStress：跨重開自動續跑、裝置清單比對、
                         事件記錄清理）
  examples/quicktool.json
                         QuickTool：script_tools 風格的完整範本（建議從這份開始）
                         分組選單、[M] 按鍵、狀態列、成功後詢問重開、Service Tag
                         輸入驗證、執行前檢查工具檔、Restarts 標籤；工具用假名
                         C:\SampleTools\Sample*64W.exe，換成自己的工具路徑即可

快速開始
--------
    雙擊 Launch-SE.ps1            （或 powershell -File Script-Web.ps1）
    瀏覽器會開啟 http://127.0.0.1:8785
    Dev-Toolbox Hub 會自動列出「Script-Editor」並代管啟動。

    命令列啟動：
    py -3 se_web.py [--port 8785] [--data-dir <資料夾>] [--open-browser]

    專案存於 <dataDir>\projects（預設 %LOCALAPPDATA%\DevToolbox\script-editor）
    自訂區塊存於 <dataDir>\blocks\<id>.json
    匯出：編輯器按「匯出 .bat」下載選單版，按「匯出 GUI .bat」下載
    視窗版；檔名取自專案名稱（需 ASCII）。

區塊包（分享自訂區塊與專案）
----------------------------
* 「匯出區塊包…」：勾選自訂區塊與已存專案，下載 <名稱>.sepack.json。勾選專案
  會自動勾選它用到的自訂區塊；內建區塊隨工具發佈，不放進包。
* 「匯入區塊包…」：選檔後先預覽，每一項標示新項目或已存在。已存在的要選
  覆蓋／改名（id 或專案名加 -2）／略過，也可一鍵套用全部；內建區塊的 id
  只能改名或略過。區塊改名時，包內專案引用它的步驟會一起改。
* 覆蓋前原檔備份到 <dataDir>\backups\pack-import-<時間>\{blocks,projects}\。
* 格式（UTF-8 JSON）：format = "script-editor-pack"、version = 1、name、
  description、createdAt、tool {id, version}、blocks、projects。不含使用者
  名稱、電腦名稱或本機路徑。
* 沒有命令列版本；API：GET /api/pack/inventory、POST /api/pack/export、
  /api/pack/preview、/api/pack/import（需 confirm: true）。

匯出腳本行為
------------
* 介面風格仿原版 script_tools.bat：==== 標題橫幅 + ---- 分隔線 +
  縮排 [N] 選項；任務 ≤9 用 choice 一鍵選（>9 退回輸入後 Enter）；
  每個任務執行前 cls；SUCCESS/FAILED 以 ==== 包圍並上色
  （VT 色彩只出現在畫面，log 檔為純文字）
* 檔頭檢查權限（requireAdmin = 以系統管理員重新啟動）
* 腳本一律在自己所在的資料夾執行（提權後也一樣），相對路徑的指令跑在腳本旁邊
* 每個步驟：顯示短指令 → 執行 → 記錄 log（腳本同目錄 <專案名>.log，log 記完整指令）
  → 依 exit code 判定 SUCCESS/FAILED。短指令取自區塊的 display 欄位，
  沒填就由指令自動產生（去掉 {TOOLS}\ 路徑、PowerShell 單行腳本只顯示一句）
* 需要工具包的區塊（勾「需要工具包」）：任務開始前先找工具包，工具以 {TOOLS}、
  工具包根以 {BUNDLE} 引用。專案設定：
    bundleRoot    auto = 自動找；或填死工具包資料夾路徑
    bundleFolder  工具包資料夾名稱，可用 *（預設 ToolBundle_*）
    bundleSub     工具所在子資料夾（預設 win64；空白 = 資料夾本身）
    bundleCheck   選填，這個檔存在才算有效的工具包
  auto 的找法：先找桌面、再找腳本所在資料夾；接受 <資料夾>\<子資料夾>，也接受
  「解壓縮全部」多一層的 <資料夾>\<資料夾>\<子資料夾>；同一處有多份時取名稱日期
  （_DD_Month_YYYY）最新的。畫面與 log 會印出找過的每個位置和略過的原因。
* 腳本支援命令列自動化：<腳本> /run <選單編號> 直接跑該任務，
  <腳本> /runall 跑全部（專案需勾選 Run all，否則回報明確錯誤）；
  Stress 循環就是靠這個跨重開自動續跑
* Stress 循環（warmboot/coldboot）：計數檔寫在腳本同目錄
  （se_cycle_*.txt），並註冊 HKLM RunOnce 讓下次登入自動重跑
  <腳本> /run N；達目標次數自動清理。中止：執行「Stop stress loop」
  任務（取消排定重開機 + 清計數 + 清 RunOnce）

GUI 視窗版（匯出 GUI .bat）
---------------------------
* 單檔 <專案名>_gui.bat：前半是批次檔（提昇權限 + 隱窗啟動 PowerShell），
  後半是 PowerShell WinForms 視窗程式（形態仿原版 script_tools_gui.bat），
  專案資料以單行 JSON 嵌在檔內
* 左側任務按鈕（含分組標題、Run all 與 Exit；會重開機的任務標橘色 Restarts）、
  上方狀態卡片（專案狀態列的輸出，F5 重新讀取）、中間彩色輸出區、下方命令列
  輸入框（Enter 或 Run 立即執行，↑↓ 翻歷史指令）；Stop 或 Esc 結束執行中的指令；
  直接按選單按鍵（1-9、字母、0）也能執行
* 步驟語意與選單版相同：執行前確認、參數輸入視窗（含 regex 驗證）、
  可接受 exit code、onFail 停止/跳過/詢問、重複執行、工具包自動搜尋
* 與選單版的差異：不支援 /run 與 /runall（自動化請用選單版）；raw 區塊
  以暫存 .cmd 重放（附選單版同款 TOOLS/BUNDLE/SE_LOG 環境，失敗/提前
  結束語意與選單版一致），含 %~（bat 自身路徑）或 {RUN}（跨重開續跑）
  的指令/raw 區塊、raw 內 exit/goto :eof/跳離暫存腳本的標籤、非 ASCII
  指令/raw 行會被明確拒絕，請改用選單版；
  參數輸入框會去除首尾空白（選單版保留），regex 驗證同為不分大小寫

注意事項
--------
* 名稱、標題、選單文字、固定參數值「必須 ASCII」：匯出的 .bat 為純
  ASCII（chcp 65001 + pipe 環境下 set /p 讀中文有相容性問題）。
* 固定值不可含 & | < > 或換行（批次會在該字元中斷）。要執行自訂命令
  請用 gen.command，內容自行確保安全；進階流程用 raw 區塊。
* 匯出腳本每行僅一個命令；多命令請拆成多個步驟，或用 raw 區塊。
* 已知限制：gen.command 與「執行時詢問」參數的值在畫面上回顯時依 cmd
  語義解析，若輸入含 & | < > 的內容會被再次解析（甚至多執行一次）。
  操作員輸入請避免這些字元；需要複雜命令請由作者寫成 raw 區塊。

開發/驗證
---------
* 後端 se_core.py（模型+驗證+.bat 匯出器，含選單版 _BatGen 與視窗版
  _GuiGen/GUI_TEMPLATE）/ se_web.py（HTTP API，/api/export 帶
  format:"gui" 匯出視窗版）。
  所有寫入 API 需 header X-Toolbox-Request: 1，僅listen 127.0.0.1。
* 內建區塊定義在 blocks\*.json（一個區塊一個檔），自訂區塊 API：
  POST /api/blocks/save、POST /api/blocks/delete。
* 範例在 examples\*.json，UI「載入範例」清單可選。
* reference\ 資料夾放參考腳本 zip（script_tools、VKEStress 等），
  不進 git。
