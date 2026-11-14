# Changelog - Script-Editor

## v0.2.0 (2026-11-14)

### 新增（區塊包）
- **匯出／匯入區塊包**（`.sepack.json`）：工具列「匯出區塊包…」勾選自訂區塊與已存專案打包
  （勾選專案會自動勾它用到的自訂區塊；內建區塊不進包）；「匯入區塊包…」先預覽每個項目是新增
  或名稱重複，重複的可選覆蓋／改名（加 `-2`）／略過（可一鍵套用全部），內建區塊的 id 只能改名
  或略過；區塊改名時包內專案引用一起改。覆蓋前原檔備份到資料資料夾
  `backups\pack-import-<時間>\{blocks,projects}\`。
  - 格式：`format`（`script-editor-pack`）、`version`（1）、`name`、`description`、`createdAt`、
    `tool{id,version}`、`blocks`、`projects`；不含使用者名稱、電腦名稱或本機路徑。
  - API：`GET /api/pack/inventory`、`POST /api/pack/export`、`POST /api/pack/preview`、
    `POST /api/pack/import`（需 `confirm: true`；還有未決定的重複項目時不寫任何檔）。
  - Script-Editor 沒有 CLI，只有編輯器按鈕與上述 API。
  - `tests/test_pack.py`：格式欄位、不含機器資訊、漏打包警告、往返匯入、dry run、重複需決定、
    覆蓋備份、改名改引用、內建 id 不可覆蓋、壞檔（非 JSON、format／version 不符、id 重複、BOM）。
- **工具包設定改成專案欄位**：`bundleFolder`（資料夾名稱，可用 `*`，預設 `ToolBundle_*`）、
  `bundleSub`（工具所在子資料夾，預設 `win64`）、`bundleCheck`（選填，找到這個檔才算有效）；
  專案面板可設定。原本寫死在程式裡的工具包名稱改由各自的專案設定。

### 變更
- **工具包區塊移出 repo**：需要特定工具包的 26 個區塊與兩個依賴它們的範例不再內建，
  改放使用者資料夾或用區塊包分享；內建區塊只剩通用、Windows 與壓力測試類。
  `examples/quicktool.json` 是建議的起點。

### 修復
- **GUI 版按「是／否」確認框就崩潰**（成功後再確認如 `Restart now?`、執行前確認）：
  `MessageBox.Show` 的圖示與預設按鈕參數順序寫反，PowerShell 把 `Question` 當成預設按鈕而丟出
  .NET 未處理例外視窗。改成 `Show(text, caption, buttons, icon, defaultButton)`。另外所有按鈕、
  按鍵、命令列與狀態讀取的處理都包上錯誤處理：發生錯誤時在輸出區與 log 印 `[ERROR]`，並跳一個
  簡短的錯誤訊息，不再出現 .NET 例外視窗。測試會檢查每個 `MessageBox.Show` 都對得到實際的多載。
- **編輯器右側設定欄寬度會跟著選取內容跳動**：三欄改成固定欄寬（300px｜自動｜400px），長字換行。
- 「載入範例」分成「我的專案（含匯入區塊包的專案）」與「內建範例」兩區；匯入區塊包後若包含專案，
  會詢問是否立即開啟。
- **自動找工具包更寬容、訊息更清楚**（選單版與 GUI 版同一套規則）：依序找桌面 → 腳本所在
  資料夾；接受 `<資料夾>\<子資料夾>` 與「解壓縮全部」多出一層的 `<資料夾>\<資料夾>\<子資料夾>`；
  同一處有多份取名稱日期（`_DD_Month_YYYY`）最新的。找的過程印出 `Looking in:` 與
  `Skipped: <路徑> - <檢查檔> not found` 完整路徑（也寫進 log），找不到時說明要放在哪裡；
  固定路徑找不到時印出實際檢查的完整路徑。原本只印預期格式、看不出到底找了哪裡。
- **GUI 版需要系統管理員權限時完全打不開**：提權那行多了一個 `\"`
  （`-Verb RunAs\"`），PowerShell 回報字串未結束，視窗不會出現。
- **提權指令改經環境變數傳路徑與參數**（`$env:SE_SELF`／`$env:SE_ARGS`），
  腳本路徑或參數含單引號（`'`）時不再截斷。選單版與 GUI 版都適用。
- **提權後工作目錄變成 System32**：選單版在開頭 `pushd "%~dp0"`（UNC 路徑也可），
  GUI 版把 PowerShell 與每個指令的工作目錄設為腳本所在資料夾；相對路徑的指令
  （例如 `dir`、同目錄的工具）現在跑在預期位置。
- 編輯器：任務一律全部展開（拿掉手風琴式收合與「N 個步驟（點選展開）」提示列），
  每個任務的步驟永遠可見、都能直接拖放，步驟可跨任務移動；沒有步驟的任務顯示放置區提示。
  任務 ↑↓ 移動後選取跟著原任務走（原本會停在原位置而編輯到別的任務）。

### 新增
- 區塊新增選填欄位 `display`（執行時畫面上顯示的短指令，例如 `Tool64W.exe ${ARGS}`）。
  沒填時自動產生：去掉 `{TOOLS}\` 與 exe 外層引號、PowerShell 單行腳本收成
  `powershell.exe -Command [inline script]`、過長截斷。畫面只印短指令，log 仍記完整
  指令；選單版與 GUI 版一致。自訂區塊編輯窗新增對應欄位，內建的 PowerShell 區塊
  補上說明式的 `display`。
- `tests/test_export.py`：提權行、工作目錄、短指令與實際執行匯出的選單版
  （由其他資料夾、路徑含空白與單引號）等測試。
- **選單分組標題**（任務 `group`）：分組變化時先印標題（例如 Windows settings／
  Service settings／System），GUI 版在按鈕列插入同樣的標題。
- **自訂選單按鍵**（任務 `key`，單一字母 B~Z，例如 `M`）：其餘任務依序自動編號，
  字母任務不佔編號；重複或與數字／`A` 衝突時驗證報錯。`/run <按鍵>` 與 `{RUN}` 跟著用。
- **主選單狀態列**（專案 `statusSteps`）：每次畫主選單前執行唯讀區塊、輸出印在選單上方
  （`/run`／`/runall` 不顯示）；只能用不需工具包的單行區塊且參數填死。
- **成功後再確認**（步驟 `askAfter`，例如 `Restart now?`）：步驟成功後才問，選否就不跑
  本任務後續步驟，任務仍算成功。
- **參數轉大寫與長度限制**（`uppercase`、`minLength`、`maxLength`，區塊定義與步驟覆蓋都可設）：
  詢問時轉大寫並檢查長度，不符合就重問；固定值在匯出時轉大寫並檢查長度。
- **執行前檢查必要檔案**（任務 `requireFiles`，可用 `{TOOLS}`／`{BUNDLE}`；`checkTools` 自動
  收集步驟指令裡的 `{TOOLS}\xxx.exe`）：缺少就列出全部缺少的檔案，任務不執行。
- **找不到工具包不算失敗**（任務 `bundleOptional`）：顯示 `[INFO]`，只略過需要工具包的步驟，
  其他步驟照跑（仿原版 [9] 狀態）。
- **結束時列出每一步的結果**（專案 `summary`，預設開）：多步驟任務結束時列出
  OK／FAILED／FAILED (continued)／SKIPPED／SKIPPED (no bundle)／not run，並寫進 log。
- 以上設定都可在編輯器設定：專案面板（狀態列、結果清單）、任務面板（分組、按鍵、必要檔案、
  檢查工具檔、工具包可選、Restarts 標籤）、步驟面板（成功後確認、參數轉大寫／長度），
  自訂區塊編輯窗（參數轉大寫／長度、會重開機）。任務清單顯示選單按鍵與分組標題。
- **GUI 版加強**：狀態卡片區（狀態列輸出的「名稱 : 值」各成一張卡片，啟動、每個任務結束後
  與 F5 重新讀取）；會重開機的任務按鈕標橘色「Restarts」（區塊 `restarts` 或任務 `restarts`）；
  Stop 按鈕與 Esc 結束執行中的指令（含其子程序）並略過剩餘步驟；鍵盤按選單按鍵即執行對應
  任務；執行中不能關閉視窗；分組標題與自訂按鍵同選單版。
- 內建 Restart Now／Restart into BIOS 區塊標記 `restarts`。
- **範本 `examples/quicktool.json`**（Windows Quick Tool）：功能與操作對應原版 script_tools 選單
  （Windows settings／Service settings／System 三組、[1]-[6] [M] [7]-[9]、狀態列、Test Signing 後
  詢問重開、Service Tag 7 碼大寫且不含母音、模式輸入驗證、[4][7][8] 標 Restarts、執行前檢查工具檔），
  工具一律用假名（`C:\SampleTools\Sample*64W.exe`），可在編輯器「載入範例」直接開來改。
- 新增通用區塊 **Run Tool**（`gen.tool`，執行指定路徑的程式＋參數）與 **Run Tool With Typed Value**
  （`gen.tool_input`，參數後面接操作員輸入的值，可設轉大寫／長度／regex）。
- **編輯器版面：區塊庫固定在左、任務在右**：1280px 以上三欄（區塊庫｜任務｜設定）；900~1279px
  兩欄（區塊庫｜任務，設定面板接在任務下方）；900px 以下才上下排列（原本 1100px 以下就上下排，
  Hub 內或高 DPI 螢幕常落到這一段，拖曳區塊要一直捲動）。區塊庫 sticky 固定在視窗內、清單自己捲動；
  工具列在窄螢幕會換行，400px 寬不再有整頁水平捲動。
- **區塊庫改成緊湊清單**：每列約 26px、單行、字小一點、bundle／raw 標籤縮小；分類標題可點擊收合／
  展開並顯示數量，收合狀態記在瀏覽器（localStorage，失敗時只記在本次頁面）；搜尋時有結果的分類
  自動展開。
- GUI 版視窗加大（1160×800，螢幕不夠高時縮到工作區高度）、按鈕加寬不再截斷名稱；狀態卡片區依
  卡片換行後的實際高度調整（原本只露出名稱）。

### 修復（續）
- 參數 regex 驗證失敗的訊息顏色碼寫成 `%%C_ERR%%`，畫面印出字面文字而不是紅色。
- 參數檢查用的 PowerShell 不再繼承 stdin（`<nul`），從檔案輸入答案時不會吃掉後面的輸入。
- 固定路徑的工具包（`bundleRoot` 非 auto）在括號區塊內用 `%TOOLS%` 判斷存在，值在區塊
  解析時就已展開而永遠判定找不到；改成區塊外判斷。
- 找工具包的子程序改用標籤跳轉、不用 `( )` 區塊：路徑或訊息裡的 `)`（例如
  `C:\Program Files (x86)`）會提早結束區塊，造成「was unexpected at this time」並中止腳本。
  步驟略過時寫進區塊的名稱（如 `Read Modes (x64)`）也會跳脫括號。
- 編輯器任務卡片被裁掉只露出上半：任務清單是有高度上限的 flex 欄，卡片預設會被壓縮；
  卡片、分組標題與區塊庫各列改為不壓縮。
- 步驟的程式是 `.bat`／`.cmd` 時改用 `call` 執行；原本控制權交給它就不會回到選單版腳本，
  後面的步驟與結果都不見了（其他指令不加 `call`，避免引號內的 `^` 被變成兩個）。

## v0.1.0 (2026-11-11)

### 修復（交付前審查）
- 可匯出字元規則補強：名稱/標題/提示/說明改檔排除 `! " % & < > ^ |`（`&`
  在 unquoted echo/title 會執行第二段指令、`>` 產生多餘檔案）；step 參數
  覆蓋的 prompt/default/validate 套用同樣規則；validate regex 字元檔真正
  排除 `'`；參數驗證錯誤不再把 regex 原文 echo 進括號區塊。
- `runAll=false` 的專案收到 `/runall`（或按 A）給明確錯誤 rc=2，不再落到
  Invalid selection。
- 暫存輸出檔改 `%RANDOM%` 並用完即刪，避免多腳本同機併跑互踩。
- Launch-SE.ps1 改傳 `-OpenBrowser`（原 `--open-browser` 綁定失敗）；自訂區塊支援雙擊開編輯窗（Update/Delete 入口）；專案名擋
  Windows 保留名（CON/NUL/COMx）；sys.bios_restart 等待改 ping（timeout /t
  不吃 stdin 重定向環境）；README 自訂區塊路徑與 /runall 說明修正。

### 使用者回饋第一批
- 匯出 .bat 選單向原版 script_tools.bat 靠攏：==== 標題橫幅、---- 分隔線、
  縮排四格 [N] 選項；任務 ≤9 改用 choice 一鍵選（按鍵即選免 Enter，>9 退回
  set /p）；每個任務執行前 cls；SUCCESS/FAILED 結果以 ==== 包圍並上色
- 編輯器任務清單改手風琴：僅展開選取任務，其餘收成一行顯示步驟數，
  任務多時不再超出畫面
- 工具包區塊依工具規格校核：補寫入語法、可接受 rc 語意、指令檔名以工具包實際內容為準、
  說明補齊（這批區塊已移出 repo，見 [Unreleased]）
- 驗證：匯出內容斷言（choice/----/縮排/====/10 任務 set/p fallback）與
  /run、/runall、無效選擇 rc=2 實跑全過；UI CDP 測試 28/28 通過

### 使用者回饋第二批 + GUI 視窗版匯出
- 編輯器 UI：任務清單與區塊庫改常駐可見捲軸（深色主題 webkit 捲軸樣式）；
  步驟列右側加 × 刪除鈕（hover／選取時顯示，免確認對話框）；新增九項選單復刻
  範例（runAll=false → 選單版匯出用 choice 1234567890；已移出 repo）
- GUI 視窗版匯出（「匯出 GUI .bat」按鈕）：單檔 bat + PowerShell WinForms，
  形態仿 reference/script_tools_gui.bat——前段批次提昇權限＋隱窗啟動，
  後段視窗：左側任務按鈕、彩色輸出區（原版控制台配色）、底部命令列輸入框
  （歷史指令 ↑↓）；專案資料以單行 JSON 嵌於檔內
  - 步驟語意鏡像選單版：confirm YesNo、參數自訂輸入對話框（validate
    regex，先套 default 再驗證、無 validate 時非空必填、不分大小寫同
    PS -match）、expectRc、onFail stop/continue/ask、loop 次數/時間、
    bundle auto 桌面搜尋；bundle 在**任務開始**就解析（同選單版，避免
    先跑副作用步驟），找不到或參數取消＝任務直接中止（不走 onFail）
  - raw 區塊以暫存 .cmd 重放，附選單版同款 TOOLS/BUNDLE/SE_LOG 環境，
    以暫存檔 rc 0/1/2/3 精確還原選單版引擎語意：raw 正常結束（含
    SE_TASK_RC≠0 時「繼續後續步驟但任務終判 FAILED」）與
    「goto {TASK}_END 提前結束（有失敗＝FAILED、無失敗＝SUCCESS 但跳过
    其後步驟）」；實測 4 種 raw 組合語意全對齊
  - 含 %~（bat 自身路徑，如 stress.device_compare 的 %~dp0）或 {RUN} 的
    指令/raw 區塊明確拒絕；非 ASCII 指令/raw 行同樣拒絕（暫存 .cmd 為
    純 ASCII）；raw 內 exit / goto :eof / 跳離暫存腳本的標籤（如選單版
    全域 goto EXIT）／動態標籤、fixed 參數值含 %~ 也一律匯出前拒絕；
    執行期路徑含非 ASCII 或 % 時 raw 重放會中止而非錯寫
  - 模板 token 改單次 regex 展開，title 含 "__DATA__" 樣文字不再二次展開
- PS 5.1 相容性修正：`New-Object Type($x.P - 1, n)` 複雜運算式當構造參數
  會誤解析（op_Subtraction on Object[]）→ 預先算好再傳；迴圈變數 $b 與
  `[bool]$B` 參數因 PowerShell 大小寫不分撞名，型別錨定把 Button 壓成
  $true → 改 $seBtn
- 驗證：GUI 三份匯出純 ASCII + PS Parser 0 錯、
  直跑與 .bat 入口冒煙（視窗出現、正常關閉、無 stderr）；stress raw 拒絕
  訊息正確；UI CDP 測試增至 34/34（新增 GUI 匯出按鈕、GUI API 匯出、
  九項選單範例、× 刪除步驟）

### 新增
- 首版：視覺化腳本編輯器（區塊庫拖曳組合任務）＋匯出免依賴選單式 .bat。
- `se_core.py`：專案/區塊模型、驗證、批次匯出引擎（SE_RUN_STEP 執行器、
  `/run <n>`、`/runall` 自動化介面、log、ok/confirm/loop/onFail 控制）。
- `se_web.py`：本機 HTTP 服務（127.0.0.1、寫入 API 需 `X-Toolbox-Request`
  header），專案 CRUD、自訂區塊、內建範例 `/api/samples`、驗證/匯出 API。
- 內建區塊 46 個：Windows/系統資訊、裝置與驅動、工具包工具（`{TOOLS}` 相對
  工具包資料夾）、通用 echo/command/停等，以及 VKEStress 風格：
  - `stress.warmboot_cycle` / `stress.coldboot_cycle`：跨重開應力循環
    （HKLM RunOnce + 腳本同目錄計數檔，登入後自動 `/run` 續跑）
  - `stress.device_snapshot` / `stress.device_compare`：裝置清單基準與比對
  - `stress.clear_loop`、`win.clear_eventlog`：中止循環/清計數、清事件記錄
- 內建範例兩組：bring-up 選單（7 任務）、RebootStress（3 任務）。
- 前端：三欄編輯器（區塊庫/任務清單/內容面板）、拖曳排序、自訂區塊
  編輯彈窗、範例挑選、驗證面板、深淺色主題（ui-kit 3.0.0）。
- 匯出 .bat 純 ASCII；名稱/標題/prompt/固定值強制 ASCII，固定值拒
  `& | < >`；`set SE_CMD=` 不加外層引號以支援含引號的指令。
- `tool.json`（Hub 自動偵測）、`Script-Web.ps1`、`Launch-SE.ps1`。

### 驗證
- 後端 API 端到端：46 區塊載入 0 錯誤；兩範例 validate 0 錯、匯出純
  ASCII；固定值含 `&` 正確拒絕；raw 區塊 `{LBL}/{TASK}/{RUN}` 代換正確。
- 匯出腳本實跑：選單分派、`/run`、`/runall`、失敗停止、log 全數正確；
  stress 安全子集（device_snapshot + device_compare）實跑通過。
- Headless Edge CDP UI 測試 28/28 通過（含範例載入、參數填死、自訂
  區塊、拖曳、驗證、主題持久化、stress 匯出內容）。
