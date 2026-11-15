# Script Editor

**Script Editor 0.2.4** 是一個用拖拉「積木」組出 Windows 工具腳本的編輯器。把常用指令做成區塊（Block），在瀏覽器裡拖進任務（Task），一鍵匯出成單一 `.bat`：選單版在命令列視窗裡用按鍵選項目，GUI 版是一個有按鈕、狀態卡片與彩色輸出的 WinForms 視窗。匯出的腳本只用 Windows 內建的 cmd 與 PowerShell，拷到測試機就能跑。

> 這是 [Dev-Toolbox](https://github.com/tpe28601/Dev-Toolbox) 的工具之一，可以從 Hub 啟動，也可以單獨使用。

## 功能

| 功能 | 說明 |
|---|---|
| **拖拉組合** | 從左邊的區塊庫拖區塊到任務裡；步驟可在任務內或跨任務拖動排序 |
| **選單版 .bat** | 主選單列出任務（分組標題、自訂字母按鍵、唯讀狀態列），支援 `/run <按鍵>`、`/runall` 自動執行 |
| **GUI 版 .bat** | 單一檔案的 WinForms 視窗：任務按鈕、狀態卡片、彩色輸出、命令列輸入框、Stop／Esc |
| **步驟控制** | 可接受的 exit code、失敗時停止／繼續／詢問、重複執行（次數或時間）、執行前確認、成功後再確認 |
| **輸入驗證** | 執行時詢問的參數可設 regex、轉大寫、長度上下限，不符合就重問 |
| **工具包** | 需要外部工具的步驟用 `{TOOLS}` 引用，執行前自動在桌面或腳本旁找工具包 |
| **區塊包** | 自訂區塊與專案打包成 `.sepack.json` 分享給同事，匯入前可預覽、處理名稱重複 |
| **範本** | `examples\quicktool.json`（Windows Quick Tool）可直接載入修改 |

### 安全原則

- 匯出的 `.bat` 為純 ASCII；名稱、標題、提示文字與固定值都會檢查，`! " % & < > ^ |` 這類會破壞批次解析的字元不能用。
- Web 服務只綁 `127.0.0.1`，寫入 API 需要 `X-Toolbox-Request: 1` 標頭，並檢查 `Host` 與 `Origin`。
- 使用者的專案與自訂區塊都放在資料資料夾，不會寫進工具資料夾。

## 需求

- Windows 10／11
- Python 3（只用標準函式庫，不需要 `pip install`）
- Windows PowerShell 5.1（啟動腳本與 GUI 版 `.bat` 使用）

找 Python 的順序：工具資料夾的 `runtime\python\python.exe` → `%DEVTOOLBOX_PYTHON%` → 工具資料夾的 `.venv` → `py` → `PATH` 上的 `python`（略過 Microsoft Store 的捷徑）。

## 快速開始

1. 啟動：
   - 從 Dev-Toolbox Hub 點 **Script Editor**；或
   - 雙擊 `Run-Script-Editor.bat`（等同 `Launch-SE.ps1`，會開瀏覽器到 `http://127.0.0.1:8785`，在視窗按 `Ctrl+C` 結束）；或
   - 命令列：`py -3 se_web.py [--port 8785] [--data-dir <資料夾>] [--open-browser]`
2. 按 **載入範例** → 內建範例 **Windows Quick Tool**。範本裡的工具都是假名（`C:\SampleTools\Sample*64W.exe`），換成自己的工具路徑即可。
3. 修改後在專案名稱欄填一個新名稱（ASCII），按 **儲存**。
4. 按 **匯出 .bat**（選單版）或 **匯出 GUI .bat**（視窗版）下載腳本。檔名取自專案名稱，GUI 版是 `<專案名>_gui.bat`。

## 編輯器操作

畫面分三欄：左邊區塊庫、中間任務、右邊設定面板（1280px 以上；較窄時設定面板移到任務下方，900px 以下改成上下排列）。

- **區塊庫**：依分類列出區塊，分類可點擊收合（記在瀏覽器）；搜尋時有結果的分類自動展開。拖到任務，或點一下加入目前選取的任務。自訂區塊雙擊可編輯，按 **＋自訂** 新增。
- **任務**：每個任務是主選單的一個項目，內含依序執行的步驟。任務一律展開，可用 ↑↓ 調整順序。
- **任務設定**：
  - 分組標題：和上一個任務不同時，選單上先印這個標題（例如 Windows settings／System）。
  - 選單按鍵：留空會自動編號 1、2、3…；也可填一個英文字母 B~Z（例如 `M`），字母任務不佔編號。`A` 保留給 Run all。
  - 執行前檢查：勾「檢查步驟用到的工具檔」會自動收集步驟指令裡的 `{TOOLS}\xxx.exe`；「其他必要檔案」每行一個路徑。缺檔時列出全部缺少的檔案，任務不執行。
  - 找不到工具包時不算失敗：只略過需要工具包的步驟，其他步驟照跑。
  - GUI 按鈕的「Restarts」標籤：不勾時依步驟自動判斷（區塊有標 `restarts` 的就顯示）。
- **步驟設定**：
  - 參數：每個參數可選「執行時詢問」或「填死」；詢問的參數可設預設值、轉大寫、長度上下限。
  - 可接受 exit code（覆蓋區塊預設）、失敗時 stop／continue／ask、重複執行（`times` 2~9999 次或 `duration` 1~1440 分鐘，可設間隔秒數）。
  - 執行前確認、成功後再確認（例如 `Restart now?`，選否就不跑本任務後面的步驟，任務仍算成功）。
- **專案設定**：視窗標題、是否需要系統管理員權限、是否有 Run all、工具包設定（見下文）、log 路徑、結束時列出每一步的結果、主選單上方的唯讀狀態列。
- **驗證**：按 **驗證** 檢查整個專案，錯誤列在專案設定面板。

## 匯出的腳本

### 選單版（`<專案名>.bat`）

- 需要權限時會以系統管理員重新啟動自己（路徑與參數經環境變數傳遞，含單引號也沒問題），之後一律切回腳本所在資料夾執行。
- 主選單：任務 9 個以內用 `choice` 一鍵選，超過改成輸入後按 Enter。每個任務執行前清畫面，結果以 `====` 包圍並上色。
- 每個步驟：顯示短指令 → 執行 → 依 exit code 判定 SUCCESS／FAILED → 寫 log。多步驟任務結束時列出每一步的結果（OK／FAILED／SKIPPED／not run 等）。
- 自動化：`<腳本> /run <按鍵>` 直接跑某個任務，`<腳本> /runall` 依序跑全部（專案需開啟 Run all）。
- 步驟的程式是 `.bat`／`.cmd` 時自動加 `call`，跑完會回到選單。

### GUI 版（`<專案名>_gui.bat`）

- 前半是批次檔（提升權限、隱藏主控台並啟動 PowerShell），後半是 WinForms 視窗程式，專案資料嵌在檔案內。
- 左側任務按鈕（含分組標題、Run all、Exit；會重開機的任務標橘色 Restarts），上方狀態卡片（F5 重新讀取），中間彩色輸出，下方命令列輸入框（Enter 或 Run 執行，↑↓ 翻歷史）。
- 直接按選單按鍵（1-9、字母、0）也能執行；Stop 或 Esc 會結束執行中的指令與其子程序，並略過剩餘步驟。
- 步驟規則與選單版相同。按鈕或按鍵處理發生錯誤時，會在輸出區與 log 印 `[ERROR]` 並跳出簡短訊息，視窗照常可用。
- 與選單版的差異：不支援 `/run`、`/runall`（自動化請用選單版）；含 `%~`（腳本自身路徑）或 `{RUN}` 的區塊、raw 區塊內的 `exit`／`goto :eof` 等會在匯出時被拒絕。

### 工具包

區塊勾了「需要工具包」時，任務開始前會先找工具包，工具以 `{TOOLS}` 引用，工具包根目錄以 `{BUNDLE}` 引用。專案設定：

| 欄位 | 說明 | 預設 |
|---|---|---|
| `bundleRoot` | `auto` = 自動找；或填工具包資料夾的固定路徑 | `auto` |
| `bundleFolder` | 工具包資料夾名稱，可用 `*` `?` | `ToolBundle_*` |
| `bundleSub` | 工具所在的子資料夾；空白 = 資料夾本身 | `win64` |
| `bundleCheck` | 選填，這個檔存在才算有效的工具包 | （空白） |

`auto` 的找法（選單版與 GUI 版相同）：

1. 先找桌面（含 OneDrive 轉向的桌面），找不到再找腳本所在的資料夾。
2. 接受 `<資料夾>\<子資料夾>`，也接受「解壓縮全部」多出一層的 `<資料夾>\<資料夾>\<子資料夾>`。
3. 同一處有多份時，取資料夾名稱裡日期（`_DD_Month_YYYY`，例如 `ToolBundle_03_March_2026`）最新的。
4. 畫面與 log 會印出找過的每個位置（`Looking in:`）與略過的原因（`Skipped: <路徑> - <檢查檔> not found`）。

### Log

預設寫在腳本旁的 `<專案名>.log`（GUI 版是 `<專案名>_gui.log`），可在專案設定改路徑。畫面只顯示短指令，log 記完整指令與結果。顏色只出現在畫面，log 是純文字。

## 區塊包（`.sepack.json`）

用來把自訂區塊與專案分享給同事。公司或團隊專用的區塊（例如需要內部工具包的區塊）請用區塊包分享，不要放進這個 repo。

- **匯出區塊包…**：勾選自訂區塊與已儲存的專案，下載 `<名稱>.sepack.json`。勾選專案時會自動勾選它用到的自訂區塊；內建區塊隨工具發佈，不會放進包裡。專案用到的自訂區塊沒有一起打包時會提醒。
- **匯入區塊包…**：選檔後先預覽，每一項標示「新項目」或「已存在」。
  - 已存在的可選 **覆蓋**、**改名**（id 或專案名加 `-2`）或 **略過**，也可一鍵套用到全部。
  - 內建區塊的 id 只能改名或略過。
  - 區塊改名時，包內專案引用它的步驟會一起改。
  - 覆蓋前原檔備份到 `<資料資料夾>\backups\pack-import-<時間>\{blocks,projects}\`。
  - 區塊包裡有專案時，匯入完成會詢問是否立即開啟；之後也可在 **開啟…** 或 **載入範例 → 我的專案** 找到。
- **格式**（UTF-8 JSON）：`format`（`script-editor-pack`）、`version`（1）、`name`、`description`、`createdAt`、`tool {id, version}`、`blocks`、`projects`。不含使用者名稱、電腦名稱或本機路徑。
- 沒有命令列版本；API：`GET /api/pack/inventory`、`POST /api/pack/export`、`POST /api/pack/preview`、`POST /api/pack/import`（需 `confirm: true`）。

## 自訂區塊格式

一個區塊一個 JSON 檔。內建區塊在工具的 `blocks\`，自訂區塊在資料資料夾的 `blocks\<id>.json`。範例（假名）：

```json
{
  "id": "sample.tool_read",
  "name": "Read Sample Value",
  "category": "Samples",
  "desc": "讀取設定值（說明可中文，只在編輯器顯示）",
  "cmd": "\"{TOOLS}\\SampleTool64W.exe\" ${ARGS}",
  "display": "SampleTool64W.exe ${ARGS}",
  "needsBundle": true,
  "expectRc": [0, 1],
  "params": [
    { "name": "ARGS", "prompt": "Arguments", "mode": "ask", "default": "",
      "validate": "^[A-Z0-9_]+$", "uppercase": true, "minLength": 1, "maxLength": 32 }
  ]
}
```

| 欄位 | 說明 |
|---|---|
| `id` | 小寫英數字與 `. _ -`，2~48 字，不能和其他區塊重複 |
| `name` | 選單與畫面上的名稱（ASCII） |
| `category`、`desc` | 區塊庫分類與說明（只在編輯器顯示，可中文） |
| `cmd` | 單行指令；可用 `${參數名}`、`{TOOLS}`、`{BUNDLE}`。和 `raw` 二擇一 |
| `raw` | 批次指令行的清單（進階）；可用 `{LBL}`、`{TASK}`、`{RUN}` |
| `display` | 選填，執行時顯示的短指令（120 字內）；沒填時由 `cmd` 自動產生 |
| `needsBundle` | 指令用到 `{TOOLS}` 時必須為 `true` |
| `restarts` | 會讓機器重開（GUI 按鈕標 Restarts） |
| `expectRc` | 可接受的 exit code 清單，預設 `[0]` |
| `params` | 參數：`name`、`prompt`、`mode`（`ask`／`fixed`）、`default`、`validate`（regex）、`uppercase`、`minLength`、`maxLength` |

## 資料存放位置

```text
%LOCALAPPDATA%\DevToolbox\script-editor\
  projects\<專案名>.json        已儲存的專案
  blocks\<id>.json              自訂區塊
  backups\pack-import-<時間>\   匯入區塊包時被覆蓋的原檔
```

匯出的 `.bat` 由瀏覽器下載，存在瀏覽器的下載資料夾。

位置的優先順序：`--data-dir <資料夾>` → `%DEVTOOLBOX_DATA%\script-editor` → `%LOCALAPPDATA%\DevToolbox\script-editor`。

## 已知限制

- 名稱、標題、選單文字與固定參數值必須是 ASCII，且不能含 `! " % & < > ^ |`。
- 固定值不可含 `& | < >` 或換行。要執行複雜命令請用 `gen.command` 或 raw 區塊。
- 「執行時詢問」的值若含 `& | < >`，在畫面回顯時會被 cmd 再解析一次；請操作員避免輸入這些字元。
- 匯出腳本每行只有一個命令；多個命令請拆成多個步驟或用 raw 區塊。
- GUI 版不支援 `/run`、`/runall`，也不支援引用腳本自身路徑（`%~`）的區塊。
- 用 Python `urllib` 等 `Connection: close` 的用戶端連續呼叫 API 時，偶爾會被 Windows 重設連線；瀏覽器不受影響。

## 疑難排解

| 狀況 | 處理 |
|---|---|
| 找不到工具包 | 看畫面上的 `Looking in:` 與 `Skipped:`。工具包要解壓縮到桌面或腳本旁，裡面要有 `<bundleFolder>\<bundleSub>\<bundleCheck>`；多一層同名資料夾也可以 |
| `Required files not found: C:\SampleTools\...` | 用的是 QuickTool 範本的假工具路徑，換成實際路徑 |
| 舊的 `.bat` 還有已修好的問題 | 匯出的腳本不會自己更新，請用新版編輯器重新匯出 |
| 改版後編輯器畫面沒變 | 從 Hub 重開 Script Editor，再在瀏覽器按 `Ctrl+F5` |
| 啟動時說找不到 Python | 安裝 Python 3，或設定 `DEVTOOLBOX_PYTHON`，或在工具資料夾建立 `.venv` |
| 「載入範例」看不到匯入的專案 | 匯入的專案在 **我的專案** 區（或 **開啟…**），內建範例只有 repo 裡的 `examples\` |

## 專案結構

```text
se_core.py            區塊／專案模型、驗證、選單版與 GUI 版匯出器
se_pack.py            區塊包匯出／匯入
se_web.py             本機 HTTP 服務（127.0.0.1）與 API
ui\                   Web 前端（ui-kit 3.x）
blocks\               內建區塊（一個區塊一個 JSON）
examples\             內建範例（quicktool.json、stress.json）
tests\                單元測試（test_export.py、test_pack.py）
Run-Script-Editor.bat 雙擊啟動
Launch-SE.ps1         命令列入口（開瀏覽器）
Script-Web.ps1        Hub 使用的 Web 入口
tool.json             Dev-Toolbox 工具描述檔
```

## 測試

```powershell
py -3 -m unittest discover -s tests
```

測試會實際執行匯出的選單版 `.bat`（只跑 echo、dir 等無害指令），並用 PowerShell 解析 GUI 版腳本。需要 Windows。

## 版本紀錄

見 [CHANGELOG.md](CHANGELOG.md)。

## 授權

[MIT](LICENSE)。
