# Wavegen CLI

`wavegen-tool` 命令提供兩個相關介面：

- Direct CLI 識別、檢查、規劃、模擬或控制一個明確選定的儀器 Resource；
- Worker CLI 啟動或與僅限 loopback 的 Worker 通訊，其命令及生命週期介面由 [Worker 合約](../contracts/wavegen-worker-contract.md) 定義。

Direct CLI 支援不代表 Worker 亦支援。兩個 Adapter 均依賴 Core 的型號身分、能力、SCPI、VISA/Backend 行為及安全機制。

<a id="installation-and-invocation"></a>

## 安裝與呼叫

從專案根目錄執行：

```powershell
uv sync --all-extras --locked --link-mode=copy
uv run wavegen-tool --help
uv run wavegen-tool <command> --help
```

需要 Python 3.10 或更新版本。已安裝的 console entry point 也稱為 `wavegen-tool`。

一般安裝需要 `pyvisa`，不會安裝 `pyvisa-py`。使用 `--backend @py` 的開發者須在 `uv sync` 之後手動安裝：

```powershell
uv pip install pyvisa-py
```

`uv sync` 執行精確同步，可能移除手動安裝的套件；請先同步環境，再安裝 `pyvisa-py`。

預設 `--backend system` 需要系統提供的 VISA 執行環境，例如 Keysight IO Libraries Suite。它呼叫 `pyvisa.ResourceManager()` 並使用 PyVISA 自身的預設 Backend 解析，不保證選用 `@ivi`。明確指定 `--backend @py` 則呼叫 `pyvisa.ResourceManager("@py")`。Wavegen 不提供 Backend fallback。Product Live 仍接受 `system` 搭配 USB 或 TCPIP/LAN，以及 `@py` 搭配 TCPIP/LAN；`@py` 搭配 USB 遭拒絕，`@bt` 沒有 Product Live 範圍。

<a id="safe-quick-start"></a>

## 安全快速開始

離線檢查已註冊 Profile：

```powershell
uv run wavegen-tool capabilities --model keysight-33521b --json
```

不執行 VISA I/O，預覽組態設定：

```powershell
uv run wavegen-tool configure-sine `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0
```

在記憶體內模擬器執行組態：

```powershell
uv run wavegen-tool configure-sine `
  --simulate `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1 `
  --offset-v 0
```

兩種途徑都不使用硬體，皆非實機驗證。

## 安全與執行模式

### Live（實機）

未指定 `--dry-run` 或 `--simulate` 時，Direct CLI 的儀器檢查與控制命令使用明確提供的 `--resource`；若未提供，則使用操作人員設定的 `WAVEGEN_TOOL_RESOURCE` 環境變數。Core 在執行操作前驗證適用的 Backend、傳輸、身分、能力與安全條件。離線自我檢查及明確的資源列出診斷不需選定 Resource。

一般 Direct CLI 實機操作不接受規劃用型號覆寫。偵測到的實體身分為權威依據。驗證 Runner 透過其私有驗證途徑提供預期型號防護；防護可以拒絕不相符的身分，但不能覆寫偵測結果。

一般實機控制從不掃描、猜測或自動選擇 Resource。環境變數必須由操作人員設定為一個精確 Resource。僅在使用者明確呼叫 `list-resources` 時進行探索。

例如，在 PowerShell 中（將預留字串替換為儀器的 VISA Resource）：

```powershell
$env:WAVEGEN_TOOL_RESOURCE = "USB0::...::INSTR"
uv run wavegen-tool identify --json
```

命令的 `--resource` 優先於 `WAVEGEN_TOOL_RESOURCE`。若兩者都未指定可用 Resource，Direct CLI 實機操作會失敗，不會開啟 VISA 連線。Worker Live 及驗證腳本仍需要明確的 Resource 引數。

### Dry-run

波形、Sweep、List Sweep、輸出設定及 Arbitrary 命令接受 `--dry-run`。dry-run 使用精確已註冊的 `--model`、執行 Core 驗證，並回傳依序排列的 SCPI 計畫，其中 `executed` 為 false。不會建立 VISA Resource Manager、開啟 Resource、查詢身分、傳送 SCPI 或變更硬體。

規劃用型號選項：

- `keysight-33510b`
- `keysight-33512b`
- `keysight-33521b`（預設）

### Simulator（模擬器）

`--simulate` 使用記憶體內模擬器，不執行實體 VISA I/O。獨立設定支援已註冊型號 Profile，並為雙通道 33510B 與 33512B 維持獨立通道狀態。每個獨立 CLI 程序從全新狀態開始；模擬 Worker 在其程序生命週期內保留狀態。

模擬不能與 `--dry-run`、實體 Resource 或非 system Backend 合併使用。模擬器行為不會擴大 Product Live 支援。

### Direct CLI 共用選項

選項僅出現在支援它們的命令：

| 選項 | 意義 |
| --- | --- |
| `--channel {1,2}` | 所選通道；預設 `1` |
| `--resource RESOURCE` | 明確指定的 USB 或 TCPIP/LAN VISA Resource；Direct CLI 實機操作時優先於 `WAVEGEN_TOOL_RESOURCE` |
| `--backend BACKEND` | 經 Core 驗證的 VISA Backend；預設 `system` |
| `--simulate` | 使用記憶體內模擬器 |
| `--dry-run` | 不執行 VISA I/O，預覽已驗證的 SCPI |
| `--model MODEL_ID` | 已註冊的 dry-run／模擬器規劃用型號 |
| `--json` | 輸出一個 JSON 結果物件 |

33510B 與 33512B Profile 具有兩個通道。33521B 具有一個通道；Core 在實機、dry-run 及模擬均拒絕 Channel 2。

在雙通道實機上，所有所選通道的狀態變更操作皆採 fail-closed，除非頻率耦合、電壓耦合、Channel 1 追蹤及 Channel 2 追蹤均回報 OFF。Wavegen 從不自動停用這些狀態。唯讀 `status` 不需要此防護。

## 離線自我檢查

### `manifest`

不啟動 Worker 或執行 VISA I/O，印出靜態工具身分及 Worker Protocol 相容性：

```powershell
uv run wavegen-tool manifest --json
```

### `capabilities`

離線印出精確的已註冊型號身分及能力：

```powershell
uv run wavegen-tool capabilities `
  --model keysight-33521b `
  --json
```

`--model` 與 `--json` 均為必要。權威支援矩陣請參閱 [支援型號](../core/supported-models.zh-TW.md)。

<a id="visa-resource-listing"></a>

## VISA Resource 列出

### `list-resources`

列出 Backend 回報的 Resource：

```powershell
uv run wavegen-tool list-resources --backend system
```

以 JSON 列出這些 Resource 字串（仍不開啟儀器工作階段）：

```powershell
uv run wavegen-tool list-resources --backend system --json
```

若要明確查詢符合條件的 Resource，且僅顯示回應 `*IDN?` 的項目，使用 `--live-only`。這會主動開啟工作階段，**不是**被動掃描：

```powershell
uv run wavegen-tool list-resources --backend system --live-only
uv run wavegen-tool list-resources --backend system --live-only --json
```

使用另行安裝的 PyVISA-py 檢查 TCPIP/LAN 資源探索：

```powershell
uv run wavegen-tool list-resources --backend '@py' --live-only --json
```

不使用實體 VISA，檢查模擬 Resource 清單：

```powershell
uv run wavegen-tool list-resources --simulate --json
```

原始清單回傳所選 Backend 回報的 Resource 字串，不開啟儀器工作階段或傳送 SCPI。選項如下：

- `--backend BACKEND`（預設 `system`）
- `--simulate`
- `--live-only`
- `--serial-baud-rate RATE`
- `--serial-read-termination {CR,LF,CRLF,NONE}`
- `--serial-write-termination {CR,LF,CRLF,NONE}`
- `--json`

`--live-only` 是明確且需主動選擇的探索診斷。它開啟符合條件的候選 Resource，各傳送至多一次有界限的 `*IDN?` 查詢。System VISA 檢查 USB、TCPIP/LAN 及已設定的 ASRL 候選項目；`@py` 檢查 TCPIP/LAN；`@bt` 無符合條件的 live-only 連線範圍。ASRL 探索不代表 ASRL 是已允許的識別／控制傳輸方式。

使用 `--live-only` 時，每個回應項目會依型號支援狀態標記：`PRODUCT_LIVE_MODEL`（型號符合 Product 政策）、`REGISTERED_ONLY`（已註冊但非 Product Live）、`UNSUPPORTED`（未註冊的製造商／型號組合）或 `UNIDENTIFIED`（非空但無法解析的 IDN）。不支援或未識別的回應項目仍會列出。JSON 項目也提供 `model_id` 與 `model_support_status`；原始清單未執行 IDN 查詢，因此這些欄位為 `null`。清單輸出不包含序號、韌體或原始 IDN。

列出 Resource 或取得 IDN 回應，不能建立所選 Backend／傳輸方式的可用 Product Live 控制。Core 識別／控制矩陣仍為 `system` 搭配 USB 或 TCPIP/LAN，以及 `@py` 搭配 TCPIP/LAN。沒有 Backend fallback。

## 儀器檢查

### `identify`

使用 `*IDN?` 查詢一個明確 Resource，解析其精確製造商及型號：

```powershell
uv run wavegen-tool identify `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --backend system `
  --json
```

選項為 `--simulate`、`--resource`、`--backend` 及 `--json`。識別不會重設儀器、變更設定或開啟輸出。

### `status`

讀取所選通道的功能、頻率、電壓、Offset、負載與輸出狀態，而不變更儀器：

```powershell
uv run wavegen-tool status `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --channel 1 `
  --json
```

選項為 `--simulate`、`--channel`、`--resource`、`--backend` 及 `--json`。

### `readback-advanced`

檢查**目前啟用**的 Advanced Mode 設定，不重新設定儀器。回應包含 `advanced_mode` 與 `settings`。沒有啟用 Advanced Mode 時，回傳 `CW` 與空的 settings 物件。未知或衝突的儀器回應會失敗，而非回傳 CW。

```powershell
uv run wavegen-tool readback-advanced --resource "$env:WAVEGEN_TOOL_RESOURCE" --channel 1 --json
uv run wavegen-tool readback-advanced --simulate --model keysight-33512b --channel 2 --json
```

這是唯讀組態檢查，並非波形量測或與先前請求比較。絕不設定輸出、觸發或波形參數。`--simulate` Direct CLI 命令在每次呼叫時啟動全新模擬器；若要測試另一項模擬 configure job 後的讀回，請使用持續運作的 Worker 模擬器。既有 `--verify-readback` 仍是主動選擇的設定與比較操作。對啟用中的 Counted Burst 與一般 Sweep，`settings.trigger_delay_s` 回報所選通道儲存的延遲，包括先前操作保留的設定。

### `read-errors`

透過一個已識別的工作階段讀取並清空儀器系統錯誤佇列：

```powershell
uv run wavegen-tool read-errors `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --max-reads 20 `
  --json
```

`--max-reads` 預設 20，接受 1 至 100。回傳項目會從儀器移除。命令成功仍可能回報儀器錯誤，因此自動化須檢查 `has_errors`。`limit_reached=true` 表示命令未確認佇列為空。命令不傳送 `*CLS`、`*RST` 或波形／輸出寫入。

## 靜態波形設定

每個設定命令都在實機寫入前驗證完整請求，只關閉所選輸出並維持 OFF。只有 `output --state on` 會開啟輸出。

具有電壓參數的波形命令只接受一種電壓輸入形式：

- `--amplitude-vpp`，可搭配選用的 `--offset-v`；或
- `--high-level-v` 與 `--low-level-v` 同時提供。

兩種形式不可混用，且 High Level 必須大於 Low Level。Core 將 High/Low 組合正規化為振幅與 Offset。波形命令的 `--load` 為 `50` 或 `high-z`；設定儀器假設的負載，不會偵測實際連接的負載。

| 命令 | 必要波形輸入 | 其他選項 |
| --- | --- | --- |
| `configure-sine` | `--frequency-hz`、一種電壓形式 | 調變/Burst、`--phase-deg`、`--load` |
| `configure-square` | `--frequency-hz`、一種電壓形式 | 調變/Burst、`--duty-cycle-percent`、`--phase-deg`、`--load` |
| `configure-ramp` | `--frequency-hz`、一種電壓形式 | 調變/Burst、`--symmetry-percent`、`--phase-deg`、`--load` |
| `configure-triangle` | `--frequency-hz`、一種電壓形式 | 調變/Burst、`--phase-deg`、`--load` |
| `configure-pulse` | `--frequency-hz`、`--pulse-width-s`、一種電壓形式 | AM/PWM/Sum/Burst、邊緣控制、`--phase-deg`、`--load` |
| `configure-dc` | `--voltage-v` | `--load` |
| `configure-noise` | `--bandwidth-hz`、一種電壓形式 | `--load` |
| `configure-prbs` | `--bit-rate-bps`、一種電壓形式 | pattern、edge time、Burst、`--load` |

Sine、square、ramp、triangle 與 pulse 接受 -360 至 360 度的 `--phase-deg`，預設為 0。Status 不回報相位。

重要的波形專屬限制包括：

- sine、square、pulse 與 noise 在適用情況下使用所選型號已註冊的 20 MHz 或 30 MHz 頻率能力；
- ramp 與 triangle 頻率為 0.000001 Hz 至 200 kHz；
- square 占空比為 0.01% 至 99.99%，較高頻率時受 16 ns 最小脈衝寬度限制而縮小範圍；
- ramp 對稱性為 0% 至 100%；
- pulse 寬度至少為 16 ns；共用或獨立邊緣時間為 8.4 ns 至 1 微秒，並須符合週期／寬度限制；
- noise 頻寬從 0.001 Hz 起，以上限受所選型號能力限制；
- PRBS 位元速率為 0.001 至 50,000,000 bit/s，pattern 為 `PN7`、`PN9`、`PN11`、`PN15`、`PN20` 或 `PN23`，共用邊緣時間須符合單一位元週期。

50 Ω 負載設定的共用波形振幅範圍為 0.001 至 10 Vpp，且 `abs(offset) + amplitude / 2` 不得超過 5 V。高阻抗時為 0.002 至 20 Vpp，邊界為 10 V。DC 接受 50 Ω 時 -5 至 +5 V，或高阻抗時 -10 至 +10 V。各參數關係及型號專屬限制仍以 Core 為準。

代表性的 Pulse 預覽：

```powershell
uv run wavegen-tool configure-pulse `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --pulse-width-s 0.0001 `
  --amplitude-vpp 0.1 `
  --edge-time-s 0.00000001 `
  --load 50
```

其他靜態波形預覽，彼此獨立且不使用硬體：

```powershell
uv run wavegen-tool configure-square --dry-run --model keysight-33521b --frequency-hz 1000 --amplitude-vpp 0.1 --duty-cycle-percent 50
uv run wavegen-tool configure-ramp --dry-run --model keysight-33521b --frequency-hz 1000 --amplitude-vpp 0.1 --symmetry-percent 50
uv run wavegen-tool configure-triangle --dry-run --model keysight-33521b --frequency-hz 1000 --amplitude-vpp 0.1
uv run wavegen-tool configure-dc --dry-run --model keysight-33521b --voltage-v 0.1
uv run wavegen-tool configure-noise --dry-run --model keysight-33521b --bandwidth-hz 1000 --amplitude-vpp 0.1
uv run wavegen-tool configure-prbs --dry-run --model keysight-33521b --bit-rate-bps 1000000 --amplitude-vpp 0.1 --pattern PN7
```

Sine 參閱 [安全快速開始](#safe-quick-start)；Pulse 如上所示。

使用 `--leading-edge-s` 與 `--trailing-edge-s` 一起設定獨立脈衝邊緣，不能與 `--edge-time-s` 混用。實機 pulse 設定會查詢並驗證儀器適用的動態邊緣限制；dry-run 只能驗證無硬體限制。

## 調變與 Sum

靜態載波命令提供下列選用且經 Core 驗證的選項組：

| 模式 | 載波 | 必要選項組 |
| --- | --- | --- |
| AM | sine, square, ramp, triangle, pulse | `--am-frequency`、`--am-depth`；選用 `--am-type {normal,dssc}` |
| FM | sine, square, ramp, triangle | `--fm-frequency`、`--fm-deviation` |
| PM | sine, square, ramp, triangle | `--pm-frequency`、`--pm-deviation-deg` |
| FSK | sine, square, ramp, triangle | `--fsk-hop-frequency`、`--fsk-rate` |
| BPSK | sine, square, ramp, triangle | `--bpsk-phase-shift-deg`、`--bpsk-rate` |
| PWM | pulse | `--pwm-frequency`、`--pwm-deviation-s` |
| Sum | sine, square, ramp, triangle, pulse | `--sum-frequency`、`--sum-amplitude-percent` |

使用 `--am-shape`、`--fm-shape`、`--pm-shape`、`--pwm-shape` 或 `--sum-shape` 選擇內部調變波形。各接受 `sine`（預設）、`square`（固定 50% 占空比）、`up-ramp`、`triangle` 或 `down-ramp`。設定波形時須提供該模式的必要選項。Product Live 要求 `sine` 波形。dry-run 與模擬對支援的載波接受全部五種波形。

主要限制及參數關係：

- AM、FM、PM、PWM 與 Sum 調變頻率從 0.000001 Hz 起。`sine` 與 `square` 上限為 33510B、33512B 的 20 MHz 及 33521B 的 30 MHz。兩種 ramp 與 triangle 上限均為 200 kHz。
- AM 深度為 0% 至 100%。預設 AM type 為 `normal`。
- FM deviation 從 0.000001 Hz 起，上限取決於載波、載波功能上限與 15 MHz 上限。
- PM deviation 為 0 至 360 度，且載波頻率須大於調變頻率的 20 倍。
- FSK rate 為 0.000125 至 1,000,000 Hz；hop frequency 須符合所選載波能力。
- BPSK phase shift 為 0 至 360 度，rate 為 0.001 至 1,000,000 Hz。
- PWM width deviation 必須嚴格位於已驗證的脈衝寬度、週期及邊緣裕度內。
- Sum 振幅相對於載波振幅為 0% 至 100%。

下列 dry-run 規劃使用 square 調變波形的 AM。

```powershell
uv run wavegen-tool configure-sine `
  --dry-run `
  --model keysight-33512b `
  --channel 2 `
  --frequency-hz 1000000 `
  --amplitude-vpp 0.1 `
  --am-frequency 1000 `
  --am-depth 50 `
  --am-shape square --json
```

其他內部調變與 Sum 範例（各自獨立執行）：

```powershell
uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --fm-frequency 5 --fm-deviation 100

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --pm-frequency 5 --pm-deviation-deg 90

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --fsk-hop-frequency 2000 --fsk-rate 10

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --bpsk-phase-shift-deg 180 --bpsk-rate 10

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --sum-frequency 10 --sum-amplitude-percent 20
```

Pulse PWM 需要同時提供 frequency 與 width deviation。PWM 與 AM 互斥。下列 dry-run 使用 triangle 調變波形。

```powershell
uv run wavegen-tool configure-pulse `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --pulse-width-s 0.0001 `
  --amplitude-vpp 1 `
  --edge-time-s 0.00000005 `
  --pwm-frequency 5 `
  --pwm-deviation-s 0.00002 `
  --pwm-shape triangle
```

一般靜態波形或 Sweep 設定會先停用所選通道適用的既有調變狀態，再設定新模式。

## Counted Burst 與 Gated Burst

Sine、square、ramp、triangle、pulse 及 PRBS 支援 Counted Burst 與 Gated Burst。Noise 不支援。

Counted Burst 選項：

- `--burst-count`
- `--burst-period-s`
- `--burst-trigger-source {immediate,bus,timer,external}`
- `--burst-trigger-timer-s`
- `--burst-trigger-delay-s`
- `--burst-trigger-slope {positive,negative}`

Immediate 使用 count 與 period。Bus 使用 count 並省略 period/timer。Timer 使用 count 與 `--burst-trigger-timer-s`。External 使用 count，可選擇 trigger slope，並省略內部 period/timer。Count 為波形週期數，PRBS 則為位元數。

Count 為 1 至 100,000,000。Immediate period 為 0.000001 至 8000 秒。Immediate/Timer 週期必須容納完整 Burst 加上 Core 安全裕度。Counted sine 與 square 載波上限為 6 MHz。Counted Burst 僅支援有限次數，且與調變模式互斥。

`--burst-trigger-delay-s` 需要 `--burst-count` 及所選觸發來源的選項。接受 0 至 1000 秒，以精確的 4 ns 為增量。省略時保留儀器已儲存的延遲；明確傳入 `0` 會設定為零秒。此選項可用於 dry-run、模擬及 Direct CLI 貢獻者驗證。Product Live 與 Worker Live 拒絕所有明確數值，包括零。

Gated Burst 使用：

- `--gated-burst`
- 選用 `--gate-polarity {normal,inverted}`（預設 `normal`）

與所有 Counted Burst 選項互斥。兩種 Burst 模式均維持輸出 OFF，且只影響所選通道。

兩種模式均接受 `--burst-start-phase-deg`。範圍為 −360.0° 至 +360.0°，以 0.1° 為增量，預設為 0.0°。啟用 Burst 時，一般波形的 `--phase-deg`（如有提供）須保持 0°。Product Live 支援 0° 的 Burst 起始相位。非零值可用於 dry-run、模擬及 Direct CLI 貢獻者驗證。

下列為**互為替代**的 Sine 預覽，不是可合併的選項。Immediate Counted Burst 提供 period：

```powershell
uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --burst-count 3 --burst-period-s 0.1 --burst-start-phase-deg 90
```

Bus、Timer 與 External Counted Burst 使用各自的觸發參數：

```powershell
uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --burst-count 3 --burst-trigger-source bus --burst-trigger-delay-s 0.1

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --burst-count 3 --burst-trigger-source timer `
  --burst-trigger-timer-s 0.1

uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --burst-count 3 --burst-trigger-source external `
  --burst-trigger-slope positive
```

Gated Burst 獨立設定，實機 Gated 操作需要另一個實體 Gate 訊號：

```powershell
uv run wavegen-tool configure-sine `
  --dry-run --model keysight-33521b `
  --frequency-hz 1000 --amplitude-vpp 0.1 `
  --gated-burst --gate-polarity normal --burst-start-phase-deg 45.1
```

Bus 觸發的 Burst 在實機設定及另行核准的 output-on 後，需要再執行明確的整台儀器 `trigger`。External trigger/gate 操作需要相容的外部訊號來源。

## 線性與對數掃頻

Direct CLI 提供：

- `configure-sine-sweep`
- `configure-square-sweep`
- `configure-ramp-sweep`
- `configure-triangle-sweep`

必要 Sweep 選項：

- `--start-frequency-hz`
- `--stop-frequency-hz`
- `--spacing {linear,logarithmic}`
- `--sweep-time-s`

選用的共用選項包括 `--hold-time-s`、`--return-time-s`、`--trigger-source {immediate,bus,timer,external}`、`--trigger-timer-s`、`--trigger-slope {positive,negative}`、`--trigger-delay-s`、一種電壓輸入形式、`--phase-deg` 及 `--load`。Hold 與 Return 預設為零。觸發來源預設為 Immediate。Timer 間隔須為 0.000001 至 8000 秒，且至少為完整 Sweep、Hold 及 Return 的總時間。只有 External 接受 `--trigger-slope`，預設為 `positive` 且一律會寫入。External 須省略 `--trigger-timer-s`。External Sweep 可用於 dry-run、模擬與貢獻者驗證。

`--trigger-delay-s` 使用與 Counted Burst delay 相同的範圍及接收規則。適用於具有其他條件皆受支援的觸發來源的線性與對數 Sweep。List Sweep 沒有 delay 選項。

Square Sweep 增加 `--duty-cycle-percent`，在較高端點頻率驗證；Ramp Sweep 增加 `--symmetry-percent`。Sweep 為所選通道操作、保持輸出 OFF，且不提供耦合雙通道啟動。

```powershell
uv run wavegen-tool configure-sine-sweep `
  --dry-run `
  --model keysight-33521b `
  --start-frequency-hz 1000 `
  --stop-frequency-hz 10000 `
  --spacing logarithmic `
  --sweep-time-s 2 `
  --amplitude-vpp 0.1
```

其他代表性 Sweep 預覽包括線性 Square Sweep、Bus 觸發 Ramp Sweep 及 Timer 觸發 Triangle Sweep：

```powershell
uv run wavegen-tool configure-square-sweep `
  --dry-run --model keysight-33521b `
  --start-frequency-hz 1000 --stop-frequency-hz 2000 `
  --spacing linear --sweep-time-s 1 `
  --duty-cycle-percent 50 --amplitude-vpp 0.1

uv run wavegen-tool configure-ramp-sweep `
  --dry-run --model keysight-33521b `
  --start-frequency-hz 100 --stop-frequency-hz 1000 `
  --spacing linear --sweep-time-s 1 `
  --symmetry-percent 50 --amplitude-vpp 0.1 `
  --trigger-source bus

uv run wavegen-tool configure-triangle-sweep `
  --dry-run --model keysight-33521b `
  --start-frequency-hz 100 --stop-frequency-hz 1000 `
  --spacing linear --sweep-time-s 1 `
  --amplitude-vpp 0.1 `
  --trigger-source timer --trigger-timer-s 2 --trigger-delay-s 0.1
```

Bus 觸發的 Sweep 在實機設定及明確 output-on 後，需另行執行 `trigger` 命令。Timer Sweep 時序須容納 Sweep、Hold 及 Return 時間。

## Arbitrary 波形與原生 Sequence

Arbitrary 命令可用於 33512B 的 Channel 1 與 2 及 33521B 的 Channel 1 的 dry-run 與模擬器模式。Product Live 範圍請參閱 [支援型號](../core/supported-models.zh-TW.md)。

建立至少八個樣本的 UTF-8 CSV，每列一個數值，不含標頭。數值必須為有限值且位於 `[-1, 1]`。名稱須以 ASCII 字母開頭，最多包含 12 個字母、數字或底線。CSV 最多包含 16,000,000 個樣本。在所選輸出 OFF 時上傳，再以載入的名稱設定播放。

```console
wavegen-tool upload-arbitrary --name WAVE_A --file wave-a.csv --dry-run
wavegen-tool list-arbitrary-memory --simulate
wavegen-tool configure-arbitrary --name WAVE_A --sample-rate-hz 1000000 --amplitude-vpp 1 --dry-run
```

上傳只會儲存 segment。設定會選擇該 segment 並保持輸出 OFF。Arb 播放使用 Sa/s 與儀器目前的濾波器設定。Core 檢查儀器可用的點數，並計入 128 點的配置單位。重複名稱會遭拒絕。若要移除所選通道的**全部**揮發性 segment 與 Sequence，使用 `wavegen-tool clear-arbitrary-memory --simulate --confirm-clear`。Clear 關閉所選輸出並重新載入預設 Arbitrary 波形。
對於目錄中以路徑顯示的已載入檔案，播放或 Sequence 步驟應使用不含副檔名的基本名稱。比對不區分大小寫，有歧義的基本名稱會遭拒絕。

若要選擇 Keysight 內建波形而不使用 CSV，加入 `--load-builtin`：

```console
wavegen-tool configure-arbitrary --name SINC --load-builtin --sample-rate-hz 1000000 --amplitude-vpp 1 --simulate
```

內建名稱不區分大小寫：`CARDIAC`、`EXP_RISE`、`EXP_FALL`、`GAUSSIAN`、`HAVERSINE`、`LORENTZ`、`D_LORENTZ`、`NEG_RAMP` 及 `SINC`。
也可將 `Exponential Rise`、`Exponential Fall`、`D-Lorentz` 或 `Negative Ramp` 加上引號使用。Core 將缺少的波形載入所選通道，或重複使用已驗證的內建目錄項目。來源未確認的同名項目會造成錯誤。設定保持所選輸出 OFF，並保留既有記憶體。dry-run 顯示缺少時載入的途徑。

使用 `configure-arbitrary-sequence --file sequence.json` 與版本 1 文件。例如，套用此組態前請先上傳各具名 segment。

```json
{
  "version": 1,
  "name": "TEST_SEQ",
  "sample_rate_hz": 1000000,
  "amplitude_vpp": 1,
  "offset_v": 0,
  "load": "50",
  "trigger_source": "bus",
  "steps": [
    {"waveform": "WAVE_A", "play_control": "repeat", "repeat_count": 3},
    {"waveform": "WAVE_B", "play_control": "onceWaitTrig"},
    {"waveform": "WAVE_C", "play_control": "repeatTilTrig"}
  ]
}
```

一個 Sequence 接受 1–512 個步驟。每個通道的所有已儲存 Sequence 共有 512 個步驟的空間。使用 `once` 播放一次、`repeat` 搭配 1 至 1,000,000 的 `repeat_count`、`onceWaitTrig` 播放後等待觸發，或 `repeatTilTrig` 重複至觸發。Marker 固定為 `lowAtStart`。使用外部觸發時將 `trigger_source` 設定為 `"external"`，並可選擇將 `trigger_slope` 設定為 `"negative"` 或 `"positive"`（預設）。使用 Bus 時，明確執行 `wavegen-tool trigger` 以傳送整台儀器的觸發。僅透過獨立的 `output` 命令啟用輸出。

使用 `--dry-run` 驗證本機輸入並在不使用 VISA 的情況下檢查計畫。對儀器或模擬器執行時，會檢查記憶體可用性與已載入的參照。若數個模擬操作需共用揮發性目錄，請使用 simulate 模式的 Worker，因每次 Direct CLI `--simulate` 呼叫均啟動全新模擬器。Worker 請求中，CSV 使用 Worker Host 的絕對路徑傳入 `arguments.path`，Sequence 物件傳入 `arguments.document`。

## Software Sequence

使用 `run-software-sequence --file` 在一台儀器上執行依序排列的一組操作。儲存版本 1 文件，例如：

```json
{
  "version": 1,
  "loop_count": 2,
  "steps": [
    {"action": "configure-sine", "arguments": {"channel": 1, "frequency_hz": 1000, "amplitude_vpp": 0.1}},
    {"action": "output", "arguments": {"channel": 1, "enabled": true}},
    {"action": "wait", "arguments": {"seconds": 1}},
    {"action": "output", "arguments": {"channel": 1, "enabled": false}}
  ]
}
```

```powershell
wavegen-tool run-software-sequence --file sequence.json --dry-run --model keysight-33512b --json
wavegen-tool run-software-sequence --file sequence.json --simulate --model keysight-33512b --json
```

即使文件設定重複執行，dry-run 也只顯示一次可供審閱的步驟順序與排程等待總時間。模擬會跨步驟共用波形記憶體。
[Software Sequence 合約](../contracts/wavegen-worker-contract.md#software-sequences) 定義允許的動作與引數。CSV 上傳使用 `arguments.path`，接受絕對路徑或相對於目前工作目錄的路徑。`configure-arbitrary-sequence` 將既有原生 Sequence 文件放入步驟的 `arguments.document`。

實機執行使用 `--resource` 或操作人員的 `WAVEGEN_TOOL_RESOURCE`。Core 依偵測到的身分與實機政策對整個 Sequence 進行 Preflight，並要求受控輸出在開始時均為 OFF。可在可控制的步驟或等待期間按 Ctrl+C 中斷。Sequence 會嘗試關閉並確認所有曾嘗試啟用的輸出，包括失敗或中斷後。通訊中斷或程序遭強制終止可能使輸出狀態未知。設定及上傳的波形可能在失敗後仍保留。

<a id="basic-workflows"></a>

## Basic Workflows（基本工作流程）

下列範例規劃各工作流程而不開啟 VISA。以 `--simulate` 取代 `--dry-run` 即可在模擬器執行。每個命令接受 `--channel`（預設 1）與 `--json`。執行要求所選通道在開始時為 OFF，並在執行後確認 OFF。無論成功或失敗，在依賴最終輸出狀態前都應檢查 `cleanup`。

| 命令 | 範例引數 |
| --- | --- |
| `run-timed-output` | `--file waveform.json --duration-s 2 --dry-run` |
| `run-amplitude-ramp` | `--start-vpp 0.1 --stop-vpp 0.25 --step-vpp 0.1 --dwell-s 1 --dry-run` |
| `run-output-cycle` | `--on-s 1 --off-s 1 --cycles 2 --dry-run` |
| `run-waveform-sequence` | `--file waveforms.json --dry-run` |
| `run-frequency-step` | `--start-frequency-hz 1000 --stop-frequency-hz 3000 --frequency-step-hz 1000 --dwell-s 1 --dry-run` |
| `apply-quick-output-preset` | `--preset square --dry-run` |

對 Timed Output，`waveform.json` 包含一個靜態波形組態，例如 `{"action":"configure-sine","arguments":{"frequency_hz":1000,"amplitude_vpp":0.1}}`。
對 Waveform Sequence，`waveforms.json` 為最多 62 個項目的陣列。各項目包含放在 `waveform` 下的該組態，以及其各自的正值 `duration_s`。工具在波形設定之間關閉輸出。
Output Cycle 使用通道目前的波形，等待每個 ON 與 OFF 間隔，包括最後的 OFF 間隔。

Amplitude Ramp 設定 sine 的 `--frequency-hz`（預設 1000 Hz）、零 Offset 及 50 Ω 負載設定。輸入正值的步進幅度。Ramp 自動選擇方向且包含兩端點，必要時使用較短的最後一步。所有點（包含首尾）均有 dwell。124 點限制來自 250 個 Sequence 步驟：三個步驟用於 configure、ON 與 OFF，每點一次 wait，後續每點一次振幅更新。排程 dwell 總時間最多 600 秒，另加儀器通訊時間。振幅更新之間輸出保持啟用。儀器保留目前的 autorange 設定，硬體換檔時可能產生暫態。Ramp 可用於 dry-run、模擬及明確的 Direct CLI 貢獻者途徑。

Frequency Step 在輸出 OFF 時將 sine 設定於第一個頻率，再讀回起始頻率與 Immediate List Sweep 設定。啟用輸出後，才啟動一次執行的 counted 原生 List。儀器依設定的 dwell 逐點前進。工作流程允許最後一個 dwell 完成、關閉輸出，再返回 CW。文件所述排程時間涵蓋 List 本身；啟用及停用輸出也需要儀器通訊時間。

產生的 List 包含精確的停止頻率，並採用既有的 128 點及 dwell 限制。`--amplitude-vpp`、`--offset-v` 與 `--load` 選擇 sine 電壓設定。

Quick Output Preset 支援 `sine`、`square` 及 `triangle`，均設定為 1 kHz、0.1 Vpp、零 Offset 與高阻抗。Square 使用 50% 占空比。三者均保持輸出 OFF。

## Frequency List Sweep（頻率清單掃頻）

Direct CLI 提供：

- `configure-sine-list-sweep`
- `configure-square-list-sweep`
- `configure-ramp-list-sweep`
- `configure-triangle-list-sweep`

`--frequencies-hz` 是由逗號分隔的 1 至 128 個頻率清單。保留輸入順序並允許重複值。觸發預設為 Immediate，可用 `--trigger-source bus` 變更。

Immediate 需要一個共用 `--dwell-s`，範圍為 0.000001 至 1000 秒。Bus 觸發要求省略 dwell；之後每個整台儀器的 Bus 觸發會使 List 前進。List Sweep 設定本身不會傳送該觸發。

```powershell
uv run wavegen-tool configure-sine-list-sweep `
  --dry-run `
  --model keysight-33521b `
  --frequencies-hz "1000,3000,7000" `
  --dwell-s 0.005 `
  --trigger-source immediate `
  --amplitude-vpp 0.1
```

對 Bus 觸發的 List Sweep，省略 `--dwell-s`。後續每個整台儀器的 `trigger` 都會讓已設定的 List 前進；設定 List 不會傳送觸發：

```powershell
uv run wavegen-tool configure-sine-list-sweep `
  --dry-run --model keysight-33521b `
  --frequencies-hz "1000,3000,7000" `
  --trigger-source bus --amplitude-vpp 0.1
```

Square List Sweep 增加占空比；Ramp List Sweep 增加對稱性。Pulse、PRBS、Arbitrary、External、Timer 與 Manual 觸發的 List、各點獨立 dwell、List 檔案、已儲存 List 與自訂 List Sweep Marker 均不在 Direct CLI 功能介面內。

## 輸出與觸發

### `configure-output`

部分更新所選通道的輸出負載／極性／限制／autorange，並保持輸出 OFF：

```powershell
uv run wavegen-tool configure-output `
  --dry-run `
  --model keysight-33512b `
  --channel 2 `
  --load 1000 `
  --polarity inverted `
  --voltage-limit-low -1 `
  --voltage-limit-high 1 `
  --voltage-limits on `
  --autorange off
```

至少需要一個欄位。選項為 `--load <1..10000|high-z>`、`--polarity {normal,inverted}`、`--voltage-limit-low`、`--voltage-limit-high`、`--voltage-limits {on,off}` 及 `--autorange {on,off}`。啟用限制需要完整的 low/high 組合，且 high 至少比 low 高 0.001 V。限制啟用時變更負載，需要在同一請求中明確停用或完整重新設定限制。不支援 `ONCE` autorange。

### `output`

只設定所選輸出狀態：

```powershell
uv run wavegen-tool output `
  --resource "$env:WAVEGEN_TOOL_RESOURCE" `
  --channel 1 `
  --state off
```

`--state {on,off}` 為必要參數。開啟輸出絕不會隱含執行；請在明確選擇 `on` 前檢視已設定的訊號及實際負載。

### `configure-sync` 與 `sync-output`

前面板 Sync 接頭有一個整台儀器共用的 ON/OFF 狀態。兩個命令都不變更主 CH1/CH2 的輸出狀態。

```powershell
uv run wavegen-tool configure-sync --dry-run --model keysight-33512b --source-channel 2 --mode carrier --polarity inverted
uv run wavegen-tool sync-output --simulate --state on --json
```

`configure-sync` 需要 `--source-channel 1|2`、`--mode normal|carrier` 及 `--polarity normal|inverted`。單通道型號使用 CH1。設定會在套用參數前及讀回時再次驗證 Sync OFF。`sync-output --state on|off` 獨立於組態設定 Sync 狀態並驗證。兩個命令都不接受 `--channel`。

兩者都支援 `--simulate` 與 `--json`。僅設定命令支援 `--dry-run`。每次 Direct CLI 模擬都從全新狀態開始。使用模擬 Worker 可跨命令保留 Sync 設定。儀器讀回會回報接頭狀態及設定。Carrier 時序取決於啟用中的波形及操作模式。

Product Live 支援明確 Sync OFF。實機 Sync 設定或 Sync ON 請使用 Direct CLI [貢獻者驗證工作流程](../CONTRIBUTING.md#real-live)。

### `trigger`

傳送一次整台儀器的 IEEE-488.2 `*TRG`，不等待：

```powershell
uv run wavegen-tool trigger --resource "$env:WAVEGEN_TOOL_RESOURCE"
```

沒有通道選項。每個已準備且使用 Bus 觸發的通道都可能回應。命令不會變更輸出狀態。僅在模擬中可用 `--model` 選擇已註冊 Profile。

### 明確的實機輸出順序

僅在有**操作人員選定**且受允許的 VISA Resource、已驗證的接線與負載，以及安全的預定波形時，才執行以下命令。這些命令是各自獨立的呼叫；若 Shell 中斷，不會自動 output-on，也不保證 output-off：

```powershell
$env:WAVEGEN_TOOL_RESOURCE = "<your exact VISA resource>"
uv run wavegen-tool identify --json
uv run wavegen-tool configure-sine --frequency-hz 1000 --amplitude-vpp 0.1
uv run wavegen-tool output --state on
uv run wavegen-tool status --channel 1 --json
uv run wavegen-tool output --state off
```

設定使輸出保持 OFF。`output --state on` 為另行明確執行的操作；完成時一律執行 `output --state off`。對 Bus List Sweep 或 Bus Burst/Sweep，`trigger` 也是獨立操作，可能影響**每個已準備的通道**，而不僅 Channel 1。

<a id="dual-channel-operations"></a>

## 雙通道操作

這些操作在 33510B/33512B 的 dry-run 與模擬中受支援；33521B 也支援 `phase-sync`。貢獻者驗證需要既有的明確 Resource、預期型號及貢獻者授權。這些操作不會擴大 Product Live 或 Worker Live 支援。模擬器程序從全新狀態開始；相依操作請使用 Software Sequence 或持續運作的 Worker。

| 命令 | 引數 |
| --- | --- |
| `phase-sync` | 無通道引數；重設共用相位參考，保留已設定的相位值 |
| `output` | `--channel all --state on\|off` |
| `configure-frequency-coupling` | `--state on --mode ratio --ratio 2` 或 `--mode offset --offset-hz 500`；選用 `--reference-channel 1\|2`；單獨使用 `--state off` 可停用 |
| `update-coupled-frequency` | `--channel 1\|2 --frequency-hz 2000` |
| `configure-voltage-coupling` | `--state on\|off` |
| `update-coupled-voltage` | `--channel 1\|2 --amplitude-vpp 0.2 --offset-v 0.05` |
| `configure-tracking` | `--mode on\|off\|invert`；選用 `--reference-channel 1\|2` |
| `configure-tracked-waveform` | `--waveform sine --arguments-json '{"channel":1,"frequency_hz":2000,"amplitude_vpp":0.2}'`；靜態波形引數 |
| `configure-combine` | `--channel 1 --source ch2\|none` 或 `--channel 2 --source ch1\|none` |
| `dual-channel-status` | 無通道引數；兩通道狀態及耦合、追蹤與 Combine 設定 |

在 `phase-sync` 前設定兩個波形及其相對 `--phase-deg`。既有 `trigger` 傳送一次整台儀器的 Bus 觸發。Group ON 為明確操作，命令層級依序執行；不保證硬體輸出邊緣同時發生。設定與耦合更新要求兩個主輸出均已確認 OFF。Group OFF 一律嘗試關閉並驗證兩個輸出；部分 ON 失敗時會嘗試關閉兩個輸出。ON 失敗仍使該命令失敗；只有確認兩個輸出與清理皆成功時，錯誤才回報輸出 OFF。
Group ON 前，Core 除檢查拓樸及電壓外，也檢查目前的波形速率與受支援的頻率相關調變／時序設定。啟用追蹤時也會在複製前檢查參考波形目前的速率設定。Keying 檢查會驗證已設定的 FSK hop frequency 與 BPSK phase shift；只有選用內部來源時才檢查內部 keying rate。既有外部 FM、PM 或 PWM 來源的 Group ON 與 Tracking 參考檢查會驗證已設定的 deviation，而非外部輸入訊號。耦合頻率變更仍拒絕這些外部調變來源。啟用中的 Burst 檢查會連同 count 及 timing 一起驗證適用的已設定起始相位、延遲、觸發斜率或 Gate 極性；角度讀回使用目前的度／弧度單位。這些檢查不會變更觸發設定。

衝突設定會遭拒絕，而非隱含解除。聯合設定的讀回會檢查要求的拓樸變更，並保留其他耦合、追蹤與 Combine 狀態，包括未要求變更的啟用中頻率耦合關係。非解除設定也會檢查任何啟用中的電壓耦合關係。即使拓樸衝突，仍可明確解除。原生追蹤會複製參考通道設定（包含頻率 List 設定與已載入 Arbitrary 記憶體），並將 Sync 來源指定為參考通道。啟用追蹤會驗證此來源及 Sync 保持 OFF。追蹤 Pulse 的讀回在兩個通道採用既有 Pulse 時序與相位檢查。
為避免不支援的記憶體變更，工具拒絕啟用中的 Arbitrary 波形設定；支援 Arbitrary 時，兩個通道皆只允許空白目錄或預設內建 EXP_RISE 項目。不會自動清除記憶體。反相追蹤保留 DC Offset。
耦合頻率變更需要 CW 頻率模式及有效的變更後調變、占空比、Pulse 與 Burst 時序。電壓耦合維持振幅、Offset、Range、負載及單位。混合 DC／非 DC 的電壓耦合無法驗證振幅相等；耦合振幅／Offset 更新需要兩個非 DC 波形。兩個 DC 通道可使用電壓耦合設定其 Offset。Combine 將來源訊號加至接收端；不會加上來源的 DC Offset。

對 AM、FM、PM、PWM 與 Sum，使用 `--am-source`、`--fm-source`、`--pm-source`、`--pwm-source` 或 `--sum-source` 搭配 `ch1` 或 `ch2`。選擇另一通道，提供一般的 depth/deviation/amplitude，並省略內部調變頻率與波形。內部調變仍是預設值。先設定來源端，再設定接收端。設定此路由前兩個輸出必須皆為 OFF。另一通道 PM 會檢查來源頻率範圍及實際設定的載波頻率；無法確認頻率範圍的來源會遭拒絕。在兩個輸出皆為 OFF 時重新設定接收端波形以解除路由。FSK 與 BPSK 維持不變。

```powershell
wavegen-tool configure-frequency-coupling --model keysight-33512b --dry-run --state on --mode ratio --ratio 2 --json
wavegen-tool configure-sine --model keysight-33512b --dry-run --channel 2 --frequency-hz 10000 --amplitude-vpp 0.1 --am-source ch1 --am-depth 40 --json
wavegen-tool output --model keysight-33512b --simulate --channel all --state off --json
```

Sequence 與 Worker 引數由 [Worker 合約](../contracts/wavegen-worker-contract.md#dual-channel-arguments) 定義。Sequence 清理將受影響輸出關閉並保留拓樸設定。回到獨立設定或 output-on 寫入前，須明確解除拓樸。所選通道 OFF 保留原有耦合／追蹤防護，不套用額外的 Combine／另一通道調變防護。

## 選用的 Advanced Mode 讀回

受支援的 Direct CLI Advanced Mode 設定接受 `--verify-readback`。啟用時，在組態設定後的同一 VISA 工作階段查詢及比較儀器已設定的模式參數，輸出保持 OFF。涵蓋既有支援載波上的 AM/DSSC、FM、PM、FSK、BPSK、PWM、Sum、Counted/Gated Burst、線性／對數 Sweep 及 List Sweep；不引入新組合。

沒有 Advanced Mode 選項的一般靜態 sine、square、ramp、triangle、pulse 或 PRBS 設定，會在傳送任何設定前拒絕此 Flag。DC、noise 與輸出設定不接受此 Flag。

無硬體模擬器範例：

```powershell
uv run wavegen-tool configure-sine --simulate --frequency-hz 1000 `
  --amplitude-vpp 0.1 --am-frequency 10 --am-depth 80 `
  --verify-readback --json
```

成功執行驗證時，Direct CLI JSON 增加 `readback_verified: true`，文字輸出增加 `Advanced Mode readback: PASS`。不相符、格式不正確的回應或查詢失敗會回傳波形驗證錯誤（exit code 29），在可取得時包含預期及實際值。只有本次設定明確設定 Trigger Delay 時，驗證才查詢該延遲。

明確提供 delay 時，即使沒有 `--verify-readback`，仍會在設定前後檢查儀器錯誤佇列。每次檢查至多讀取一項。若首次檢查發現佇列內的錯誤，命令會回報並在寫入任何設定前停止。第二次檢查回報寫入後讀到的任何錯誤。使用 `read-errors` 檢查剩餘項目。

`--simulate` 途徑只檢查記憶體內模型。即使有 `--verify-readback`，`--dry-run` 仍回傳一般離線計畫，不宣稱執行了讀回。已設定參數的驗證不能證明類比波形準確度或實際外部 trigger/gate 行為。

## JSON 與錯誤

使用 `--json` 取得 Direct CLI 命令的一個 JSON 物件。不要將人員可讀的輸出解析為穩定的機器 API。引數解析錯誤維持標準 argparse stderr/usage 行為；解析成功後，JSON 模式將結果保留在 stdout。

Direct CLI exit code 分類如下：

| 代碼 | 意義 |
| ---: | --- |
| 0 | 成功 |
| 2 | CLI 使用方式或波形參數錯誤 |
| 10 | 不支援的傳輸方式 |
| 11 | 不支援的連線範圍 |
| 20 | Resource Manager 錯誤 |
| 21 | Resource 開啟錯誤 |
| 22 | IDN 查詢錯誤 |
| 23 | IDN 格式不正確 |
| 24 | 不支援的儀器 |
| 25 | VISA 清理錯誤 |
| 26 | 資源探索錯誤 |
| 27 | VISA 寫入錯誤 |
| 28 | 狀態查詢錯誤 |
| 29 | 波形驗證錯誤 |
| 30 | 錯誤佇列查詢錯誤 |
| 70 | 內部錯誤 |

## Worker CLI

Worker 是僅限 loopback 的本機 HTTP 控制介面。型號接收遵循 Core：dry-run 與模擬器請求接受已註冊的 33510B、33512B 及 33521B 規劃用型號；Product Live 接受 33512B 及 33521B。33510B 仍不提供 Product Live。

啟動模擬 Worker：

```powershell
uv run wavegen-tool worker --mode simulate --control-port 8765
```

Worker 啟動選項：

- `--mode {live,simulate}`（必要）
- `--resource`（live 必要，simulate 禁止；Worker 不讀取 `WAVEGEN_TOOL_RESOURCE`）
- `--backend`（預設 `system`）
- `--control-port`（預設 `0`；零會選擇可用的 loopback Port）
- `--allow-output-writes`（預設停用）

Worker 啟動會驗證接收條件，但不開啟實機 Resource 或識別儀器。`ready` 生命週期事件表示 loopback HTTP 控制介面正在提供服務；並不代表實體硬體已連接或識別。

31 個領域命令及其請求欄位請參閱 [Worker 合約](../contracts/wavegen-worker-contract.md)。Worker 命令名稱使用 kebab-case，請求引數欄位使用 snake_case。所選通道結果保留 Core 的整數 `channel`，預設 1。Bus `trigger` 適用於整台儀器。

實機波形設定、Sync 設定及 trigger 需要 Worker 以 `--allow-output-writes` 啟動。模擬 Arbitrary Clear 需要在請求中提供 `confirm_clear: true`。主輸出 ON 與 Sync ON 同時需要 `--allow-output-writes` 及 `confirm_output: true`。唯讀命令及明確主輸出 OFF、Sync OFF 不需要啟動 Flag。

Worker 有一個作用中 job slot。第二個請求會以 `busy` 拒絕，或在 cooperative stop 請求後以 `stopping` 拒絕。

每個 `POST /command` 都提供請求 Context。`simulate` 與 `dry_run` Context 需要精確已註冊的 `planning_model_id`，並須省略 `expected_model_id`。dry-run 絕不綁定模擬器狀態。第一個被接受的 simulate job 將 Worker 綁定至其規劃用型號，直到程序結束；後續請求不能擅自切換型號。

`live` Context 可選擇提供精確已註冊的 `expected_model_id` 作為型號不符防護，並須省略 `planning_model_id`。偵測到的 `*IDN?` 身分與 Core Product 政策仍為權威依據。省略防護不會預設為 33521B。精確的 Context 與引數 Schema 仍由合約管理。

被接受的命令回傳 HTTP 202，並以非同步方式執行。格式不正確或不符合接收條件的請求回傳 HTTP 400；busy 或 stopping 的接收結果回傳 HTTP 409。Worker 不建立每個 job 的產物。

Worker HTTP 請求／回應及機器模式 stdout JSONL 使用 Schema 版本 3。同一程序的事件共用 `run_id`，包含 `ready`、`job_accepted`、`job_started`、`job_finished`、`stop_requested`、`error` 與 `summary` 等生命週期事件。
`job_finished` 是成功及失敗 async job 的共用終止事件。人員可讀診斷不是機器 API。

Common `worker_state="running"` 搭配 `fatal_error=null` 表示控制介面已就緒，即使 job 正忙碌亦然；不代表命令 slot 閒置。

### Worker 生命週期 Client

提交一個領域命令：

```powershell
uv run wavegen-tool send-command `
  --port 8765 `
  --command status `
  --context-json '{"mode":"simulate","planning_model_id":"keysight-33521b"}' `
  --json
```

`send-command` 需要 `--port`、`--command` 及 `--context-json`。也接受 `--arguments-json`（預設 `{}`）、`--job-id`、`--timeout-ms`（預設 1000）及 `--json`。提交不會等待 job 完成。

- `worker-status` 讀取僅存於記憶體的生命週期狀態；並非儀器狀態。接受必要 `--port`、選用 `--timeout-ms`（預設 1000）及選用 `--json`。
- `wait-ready` 持續輪詢，直到命令 slot 閒置或達到有界限的期限；即使 Common 控制介面就緒，job 忙碌時仍繼續等待。除了 `--port`、`--timeout-ms` 及 `--json`，還增加 `--wait-timeout-ms`（預設 30000）及 `--poll-ms`（預設 200）。
- `worker-stop` 請求 cooperative stop。不等待程序結束，也不自動關閉輸出。接受 `--port`、`--timeout-ms` 及 `--json`。

使用 `--json` 時，每個生命週期 Client 輸出一個精簡的 Schema 版本 2 JSON 物件。原始 Worker v3 HTTP 回應保留於 `worker_response`。被接受的 `send-command` 結果確認的是接收，而非 job 完成；使用生命週期狀態觀察結果。

一般實機關閉程序，應在停止前提交明確 output-off。精確 Schema、生命週期意義及佇列規則請參閱 [Wavegen CLI JSON / JSONL 合約](../contracts/wavegen-cli-jsonl-contract.md)、[Worker 合約](../contracts/wavegen-worker-contract.md)、[Wavegen Orchestrator 工作流程](../contracts/wavegen-orchestrator-workflows.md) 及 [Common Protocol](../contracts/common-worker-protocol.md)。

### 模擬 Worker 操作範例

在一個 PowerShell 終端機啟動本機 Worker，讓它持續執行：

```powershell
uv run wavegen-tool worker --mode simulate --control-port 8765
```

在另一個終端機等待就緒、提交模擬設定、觀察 job 完成，再請求 cooperative stop：

```powershell
uv run wavegen-tool wait-ready --port 8765 --json

uv run wavegen-tool send-command `
  --port 8765 --command configure-sine `
  --context-json '{"mode":"simulate","planning_model_id":"keysight-33521b"}' `
  --arguments-json '{"frequency_hz":1000,"amplitude_vpp":0.1}' --json

uv run wavegen-tool wait-ready --port 8765 --json
uv run wavegen-tool worker-status --port 8765 --json
uv run wavegen-tool worker-stop --port 8765 --json
```

這只使用模擬器，不使用實機。`send-command` 回傳 HTTP 202 表示接收而非完成；後續 readiness/status 呼叫提供生命週期資訊。Worker stop 請求不會關閉實體輸出：停止 **Live** Worker 前，須傳送帶有 `{"enabled":false}` 的明確 `output` 命令並等待完成。

## CLI 驗證腳本

這些貢獻者腳本獨立於一般 Product Live，且在實體 VISA I/O 前需要操作人員授權。它們共用一份 Full 案例清單。

| 腳本 | 用途 | 實體 VISA |
| --- | --- | --- |
| `scripts/preflight-cli.ps1` | 離線能力、CLI dry-run 與模擬器案例 | 不使用 |
| `scripts/live-cli-check.ps1 -PlanOnly` | 檢視所選案例、精確預定連線及風險 | 不使用 |
| `scripts/live-cli-check.ps1` | 互動式、有界限的實機驗證 | 僅在另行核准後 |

### Full 驗證模式

`-Modes` 僅可搭配 `-Suite full`。未指定 `-Modes` 的 Full 執行會依標準順序選取所有適用功能系列。可使用單一模式、逗號分隔清單或 PowerShell 陣列；重複項目只執行一次。無效或空白的選擇，或 Smoke 與 Modes 合併使用，均採 fail-closed。無論選擇何種模式，共用安全、身分及最終錯誤佇列案例仍為必要。

| 模式 | 涵蓋範圍 |
| --- | --- |
| `static` | Sine、Square、Ramp、Triangle 及 Pulse 的非零相位、DC、Noise、全部六種 PRBS pattern 與非預設 PRBS edge time；33521B 相位同步 |
| `modulation` | AM/DSSC、FM、PM、FSK、BPSK、PWM 與 Sum，包含代表性載波及受支援的非 sine 內部調變波形 |
| `burst` | Counted 與 Gated Burst、代表性載波及觸發來源、明確零及非零 trigger delay、Counted 90° 與 Gated 45.1° 起始相位 |
| `sweep` | Sine、Square、Ramp、Triangle 的線性及對數 Sweep、代表性觸發、明確零及非零延遲、Square 占空比與 Ramp 對稱性 |
| `list-sweep` | 原生頻率 List 與 Bus 觸發、恢復 CW |
| `output` | 輸出設定，包含 1000 Ω 負載、受控 ON/OFF，以及代表性的 Sync 來源、模式及極性設定 |
| `arbitrary` | 合成 CSV 上傳、記憶體檢查、播放、經確認的揮發性記憶體 Clear、所有受支援的內建波形與 SINC 重複使用 |
| `sequence` | 原生 Bus 及兩種 External 觸發邊緣、支援型號的代表性 CH2 Bus Sequence，以及 once、repeat、onceWaitTrig、repeatTilTrig |
| `software-sequence` | 依序排列的軟體控制動作、受支援的 Arbitrary 操作及確認輸出清理，包含雙通道型號的聯合輸出 |
| `dual-channel` | 33510B/33512B 的雙通道相位、Group Output 及 Bus 觸發、頻率／電壓耦合、追蹤、另一通道調變及 Combine |
| `basic-workflows` | 定時輸出、雙向 Amplitude Ramp 與 Frequency Step、Output Cycle、Waveform Sequence、Sine/Square/Triangle Quick Output Preset，以及模擬 Worker 取消與停止 |

Full Static 與 Sweep 案例使用 `--verify-waveform-readback`，在輸出 OFF 時比較已設定的 Square、Ramp、Triangle 相位、PRBS pattern 及 edge time、Square Sweep 占空比及 Ramp Sweep 對稱性與讀回值。Pulse 相位使用 Pulse 設定已內建的讀回。Preflight 檢查模擬讀回，PlanOnly 顯示預定驗證。

`-Suite full -Modes burst` 包含兩個非零 Burst 起始相位案例。這些貢獻者驗證案例保持輸出 OFF，透過讀回驗證已設定相位。量測實體波形行為需要另行授權的觀察。每當選擇 Burst，Preflight 也會檢查無效相位遭拒絕。在獲授權的 Real Live 執行前，Preflight 與 PlanOnly 使用相同模式選擇。

33512B 接受 CH1/CH2 Arbitrary；33521B 接受 CH1；33510B 沒有 Arbitrary 能力。All-target Preflight 依 Core 能力選取適用案例；對單一目標明確要求不支援的 Arbitrary/Sequence 會遭拒絕。所選子集 PASS 僅證明該子集。Arbitrary/Sequence 實機執行使用僅供貢獻者的 Core 途徑，而非一般 CLI 或 Worker Product Live。只有 Real Full 可在獨立案例間重設，為經授權的觀察試驗明確啟用輸出，且須獨立確認安全 OFF。所選通道的揮發性記憶體 Clear 會刪除全部 segment 與 Sequence；PlanOnly 會指出此操作，Real Live 則要求操作人員在該步驟另外輸入 `CLEAR`。

### 腳本參數

| 參數 | Preflight | Live/PlanOnly | 預設值／限制 |
| --- | --- | --- | --- |
| `-Suite` | 選用 | 選用 | `smoke`；另一個值為 `full` |
| `-Modes` | 僅 Full | 僅 Full | 省略時選取所有適用功能系列；逗號分隔或陣列 |
| `-Target` | 選用 | 必要 | Preflight 為 `all`；Live 為一個精確已註冊型號 |
| `-Connection` | 不使用 | 必要 | `usb` 或 `tcpip`（LAN），須符合 Resource 前綴 |
| `-Resource` | 不使用 | 必要 | 操作人員明確選擇的 VISA Resource，包含 PlanOnly |
| `-Backend` | 不使用 | 選用 | `system`；Full 需要 System VISA |
| `-PlanOnly` | 不使用 | 選用 Switch | 不建立 VISA Manager、不執行硬體 I/O、重設或輸出 |
| `-Python` | 選用 | 選用 | `.\.venv\Scripts\python.exe`；Full 需要專案 Python |
| `-OutputRoot` | 選用 | 選用 | `.tmp_tests\cli_preflight` / `.tmp_tests\cli_live` |
| `-DebugArtifacts` | 選用 | 選用 | 預設停用；額外私人診斷及去識別化的公開證據 |

`Full` 另外需要 Windows PowerShell Desktop 與乾淨、未變更的 Git HEAD。一般 Smoke 不要求乾淨的 Git 工作樹。腳本不會掃描或推斷 Resource；重新導向的 stdin 無法核准 Real Live 執行。
Real Full 前應檢查示波器／終端負載、適用時的外部觸發來源、輸出／接地限制，以及手動安全關閉輸出的應變措施。

從專案根目錄執行的範例：

```powershell
# Existing representative Sine smoke; no hardware.
.\scripts\preflight-cli.ps1

# Every applicable family on every registered model, hardware-free.
.\scripts\preflight-cli.ps1 -Target all -Suite full

# Focused hardware-free Full preflight.
.\scripts\preflight-cli.ps1 -Target keysight-33512b -Suite full -Modes arbitrary,sequence

# Hardware-free Full plan with explicit intended resource (not opened).
.\scripts\live-cli-check.ps1 -Target keysight-33512b -Connection usb `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" -Backend system `
  -Suite full -Modes arbitrary,sequence -PlanOnly

# Full plan of all applicable modes: omit -Modes.
.\scripts\live-cli-check.ps1 -Target keysight-33521b -Connection usb `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" -Suite full -PlanOnly
```

僅於另行授權的互動式實機工作階段移除 `-PlanOnly`。不得將 Preflight、PlanOnly、模擬、所選模式的 Real Live 或僅供貢獻者的 Arbitrary 案例視為自動授予 Product Live。

這些腳本是貢獻者工具，並非每個 PR 的一般要求。證據接收與隱私政策請參閱 [Contributing](../CONTRIBUTING.md)。

`smoke` 保留代表性的各通道 Sine 基本檢查；**smoke PASS != all implemented features validated**（Smoke 通過不代表所有已實作功能均已驗證）。`full` 使用共用標準案例清單驗證目前已實作的 Direct CLI Live 功能系列，以 CH1 為主要涵蓋範圍，CH2 僅做必要的對等驗證。

Full 在建立產物前需要可讀取的 Git HEAD 及乾淨的工作樹，並記錄 `git_head` 與 `git_clean=true`。Smoke 不要求乾淨的工作樹。Full Preflight 亦需要 Windows 上的 Windows PowerShell Desktop 與專案 Python 環境。

Full 需要 Windows PowerShell、專案 `.venv/Scripts/python.exe` 及 `-Backend system`。使用 `-Connection usb` 對應明確的 `USB...` Resource，或 `-Connection tcpip` 對應明確的 `TCPIP...`（LAN）Resource。Real Live 偵測到的身分須精確符合 `-Target`。若要進行 Full 接收驗證，請對下列每個命令加入 `-Suite full`。Preflight Full、PlanOnly Full 與 Real Full 均須通過，所有必要案例 PASS、清理成功、確認輸出安全 OFF、最終錯誤佇列乾淨，以及成功產生可分享證據。對目前已允許的 Product Live 功能系列而言，這是供另行審查型號／版本的合格證據，不代表自動擴大支援。僅供貢獻者的 Arbitrary/Sequence 結果不開放 Product Live；33510B 仍不具 Product Live 資格。
參閱 [完整案例清單與接收規則](../CONTRIBUTING.md#real-live)。

### 無硬體 Preflight

```powershell
.\scripts\preflight-cli.ps1 -Target all
```

`-Suite` 接受 `smoke`（預設）或 `full`。`-Target` 接受 `all` 或一個已註冊 model ID。`-Python` 與 `-OutputRoot` 為選用：Python 預設為 `.\.venv\Scripts\python.exe`，輸出根目錄預設為 `.tmp_tests\cli_preflight`。Preflight 執行能力、dry-run 及模擬器檢查，不需要 Resource 或硬體 I/O。

### PlanOnly

```powershell
.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Backend system `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" `
  -PlanOnly
```

PlanOnly 需要精確預定 Resource，但不開啟 VISA、接觸硬體或傳送實機 SCPI。Console 與 shareable 輸出會遮蔽 Resource；`-DebugArtifacts` 將它保留在本機私人證據中。

Live Runner 的參數：

| 參數 | 行為 |
| --- | --- |
| `-Suite` | `smoke`（預設）或 `full` |
| `-Modes` | 僅 Full 選用；單一／逗號分隔模式，預設所有適用模式 |
| `-Target` | 必要的精確 model ID；拒絕 `all` |
| `-Connection` | 必要的 `usb` 或 `tcpip`，須符合 Resource 前綴 |
| `-Resource` | 必要的操作人員提供的精確 Resource |
| `-Backend` | 選用；預設 `system` |
| `-PlanOnly` | 選用的無硬體規劃 Switch |
| `-DebugArtifacts` | 選用的詳細私人及去識別化命令產物，也傳給 Preflight |
| `-Python` | 選用；預設 `.\.venv\Scripts\python.exe` |
| `-OutputRoot` | 選用；預設 `.tmp_tests\cli_live` |

### Real Live

僅在需要明確的硬體驗證時移除 `-PlanOnly`。任何 I/O 前，Runner 印出精確目標、連線、Backend、Resource、通道、案例、代表性波形及安全計畫。拒絕重新導向輸入；按 Enter 啟動，Ctrl+C 取消。Runner 不掃描、猜測或自動選擇 Resource。Smoke 保持輸出 OFF。Full 僅為輸出控制及經確認的觀察案例使用輸出。Real Full 需要示波器及另一個 Ext Trig/Gate 來源。未確認的觀察會使 Full 失敗。

兩個腳本一般保留 `shareable/report.json` 與 `shareable/summary.md`。使用 `-DebugArtifacts` 取得詳細診斷。所有 `private/` 產物應保留在本機。產物處理、證據接收及 Product Live 決策請參閱 [Contributing](../CONTRIBUTING.md)。

### Full 與 Debug 範例

下列**無硬體** Full 檢查需要乾淨的 Git 工作樹，以及文件所述 Windows PowerShell／專案 Python 參考環境：

```powershell
.\scripts\preflight-cli.ps1 -Target all -Suite full -DebugArtifacts

.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Backend system `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" `
  -Suite full `
  -PlanOnly
```

`-DebugArtifacts` 為選用，也可在 Live Runner 使用，以保留詳細私人診斷。PlanOnly 需要預定 Resource，但不開啟它。

**Real Live Full 是需主動同意的硬體操作**，不是一般文件或 CI 檢查。僅在準備實體儀器、示波器、獨立外部 Trigger/Gate 來源、乾淨的 Git 工作樹及操作人員確認後，才可不帶 `-PlanOnly` 呼叫同一 Runner：

```powershell
.\scripts\live-cli-check.ps1 `
  -Target keysight-33512b `
  -Connection usb `
  -Backend system `
  -Resource "$env:WAVEGEN_TOOL_RESOURCE" `
  -Suite full
```

不同於一般 CLI/Worker 控制，明確啟動的 Real Live Full 在確認主輸出與 Sync 皆 OFF 後，於**每個獨立案例**前執行有界限的 `*RST`。等待 `*OPC?`、再次關閉 Sync、驗證安全 OFF 狀態並檢查有界限的錯誤佇列。**重設可能短暫啟用 Sync BNC。確認 Real Full 前須檢查接線並斷開敏感設備。** 不安全或失敗的重設會阻止後續案例。
Real Full 可為允許的觀察案例明確啟用主輸出，這些案例需要示波器觀察與安全 OFF 清理。
專用 Sync 控制案例透過儀器讀回檢查 Sync ON 再 OFF。只有 Real Live Full 執行此重設。
