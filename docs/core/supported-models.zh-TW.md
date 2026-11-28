# 支援型號

本頁為 Wavegen Tool 面向使用者的權威支援矩陣。型號註冊、無硬體規劃、模擬、貢獻者驗證與 Product Live（正式實機支援）是不同的範圍。

## 支援矩陣

| 型號 | Model ID | 通道數 | 頻率能力 | 已註冊 | Dry-run | 模擬器 | Product Live |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 33510B | `keysight-33510b` | 2 | 20 MHz | 是 | 是 | 是 | 否 |
| 33512B | `keysight-33512b` | 2 | 20 MHz | 是 | 是 | 是 | 是 |
| 33521B | `keysight-33521b` | 1 | 30 MHz | 是 | 是 | 是 | 是 |

頻率值為 Core Registry 對 sine、square、pulse 與 noise 操作的共用上限。每種波形、調變、Burst 及 Sweep 也會套用各自請求的限制。命令詳細資訊請參閱 [CLI 指南](../cli/README.zh-TW.md)。

33510B 的精確已註冊 Profile 有僅供貢獻者使用的驗證途徑，只能透過明確的貢獻者工作流程使用，不會改變預設 Product 政策，也不會讓該型號成為 Product Live。驗證證據處理與支援決策請參閱 [Contributing](../CONTRIBUTING.md)。

## 精確實機身分

Core 識別以下製造商欄位：

- `Keysight Technologies`
- `Agilent Technologies`

型號必須精確解析為 33510B、33512B 或 33521B。製造商比對忽略大小寫並將一般空白合併；型號比對忽略大小寫及前後空白。兩者均不使用前綴、子字串、模糊比對、別名、Resource 名稱或猜測比對。一般 Product Live 接收再將偵測到的型號限制為 33512B 或 33521B。

受支援實機所回報的製造商會保留在身分結果中，型號則正規化為已註冊的標準形式。

## 無硬體支援

三種已註冊 Profile 皆可明確選用於支援的 dry-run 與獨立模擬器設定途徑。雙通道 33510B、33512B Profile 維持獨立的模擬通道狀態。單通道 33521B 在 dry-run、模擬器及實機途徑均拒絕 Channel 2。

dry-run 不執行 VISA I/O。模擬器結果是具確定性的測試行為，並非實體型號、Backend、連線或命令系列已驗證的證據。

三種已註冊型號的 dry-run 與模擬均支援 AM、FM、PM、PWM 和 Sum 的 sine、square、rising ramp、triangle 及 falling ramp 調變波形，並套用各模式的載波限制。

Arbitrary 命令在 dry-run 與模擬支援 33512B 的 Channel 1 和 2（160 MSa/s），以及 33521B 的 Channel 1（250 MSa/s）。標準 33510B Profile 拒絕 Arbitrary。

## Product Live 連線範圍

直接 Product Live 識別及控制使用明確指定的 USB 或 TCPIP/LAN VISA Resource，並遵循以下 Backend 接收規則：

| Backend 選擇器 | Core 身分 | USB 接收 | TCPIP/LAN 接收 |
| --- | --- | ---: | ---: |
| 省略或 `system` | `system_visa` | 接受 | 接受 |
| `@py` | `pyvisa_py` | 拒絕 | 接受 |
| `@bt` | `pyvisa_bt` | 拒絕 | 拒絕 |

這些項目描述 Core 的傳輸／Backend 接收範圍。實際操作仍須符合允許的型號、命令、通道及安全狀態；表格不代表每種組合均具有獨立硬體驗證紀錄。`@bt` 是可識別的 Backend 身分，但沒有 Product Live 或實機驗證開放範圍。直接識別／控制拒絕 GPIB、ASRL、PXI、VXI、未知的傳輸方式，以及不支援的 Backend／傳輸組合。沒有自動 Backend fallback。

一般實機控制不會掃描、推斷、猜測或自動選擇 Resource。資源探索僅由明確的 `list-resources` 診斷命令進行，並不授予直接控制支援。

## Product Live 功能範圍

對已允許的 33512B 與 33521B Resource，Product Live 使用相同的 Core 請求驗證與安全規則，適用於：

- 靜態 sine、square、ramp、triangle、pulse、DC、noise 及 PRBS；
- 以 sine 為調變波形的 AM、FM、PM、PWM 及 Sum 支援載波組合，以及 FSK、BPSK 支援載波組合；
- 支援載波上起始相位為 0° 的 Counted Burst 與 Gated Burst；
- sine、square、ramp 及 triangle 的一般 Sweep（Immediate、Bus 或 Timer），以及 List Sweep（Immediate 或 Bus）；
- 所選通道的狀態、輸出設定及輸出控制；
- 明確的前面板 Sync OFF 控制；
- 有界限的錯誤佇列讀取、身分識別及整台儀器的 Bus 觸發。

Product Live 在組態寫入前拒絕非零 Burst 起始相位與非 sine 的 AM、FM、PM、PWM 及 Sum 調變波形。這些選項可用於 dry-run、模擬及 Direct CLI 貢獻者驗證。

Direct CLI 與 Worker 在 dry-run 及模擬中提供 Arbitrary 操作。Core 在所有型號的 Product Live 模式均拒絕這些操作。對 33510B、33512B 及 33521B，Counted Burst 與一般 Sweep 的明確 Trigger Delay 在既有型號及連線規則下可用於 dry-run、模擬及 Direct CLI 貢獻者驗證。Product Live 與 Worker Live 在組態寫入前拒絕明確的數值（包含零）。省略延遲時保留已儲存的設定。

一般 Sweep 的 External 觸發可用於 dry-run、模擬及 Direct CLI 貢獻者驗證。Core 在 Product Live 拒絕此功能。前面板 Sync 設定及 Sync ON 可用於模擬與 Direct CLI 貢獻者驗證；Sync 設定也支援 dry-run。

本概覽不會移除各命令的專屬限制。無效的載波、選項組合、通道、數值、觸發來源或安全狀態仍會採 fail-closed（預設拒絕）。Product Live 表示型號符合目前 Core 政策，並非宣稱每種命令／Backend 組合均已在硬體上獨立驗證。

## 雙通道操作

33510B 與 33512B 支援明確的相位同步、透過既有波形命令設定相對相位、整台儀器的 Bus 觸發、聯合主輸出、頻率比例／偏移耦合、電壓耦合、追蹤（包含反相追蹤）、以另一通道為來源的 AM/FM/PM/PWM/Sum、Combine 及雙通道狀態。33521B 支援相位同步，但拒絕僅限雙通道的命令。

新操作及另一通道調變僅可用於 dry-run、模擬及 Direct CLI 貢獻者驗證。一般 Product Live 與 Worker Live 維持不變。既有的 Bus 觸發與相位參數保留先前的接收範圍。無硬體驗證不是實體時序、極性、調變或耦合效能的證據。

## 所選通道安全

對雙通道實機，Core 要求頻率耦合、電壓耦合、Channel 1 追蹤及 Channel 2 追蹤皆回報 OFF，才能執行獨立的狀態變更操作。在 Core 貢獻者驗證模式，雙通道儀器的 Arbitrary 波形與 Sequence 播放組態也要求 Arbitrary 取樣率耦合回報 OFF。Core 不會自動停用耦合或追蹤。獨立設定及 output-on 寫入也拒絕啟用中的 Combine 或另一通道調變路由。所選通道 OFF 保留原有的耦合／追蹤防護，不套用額外跨通道相依防護。追蹤要求沒有啟用中的 Arbitrary 波形，且在支援 Arbitrary 時兩通道皆僅有預設內建 EXP_RISE 項目（或空白目錄）。耦合頻率變更要求 CW 頻率模式，且變更後的調變與脈衝時序有效。混合 DC／非 DC 的電壓耦合因無法驗證相等振幅而遭拒絕；耦合振幅／Offset 更新要求兩個非 DC 波形。兩個 DC 通道可使用電壓耦合設定其 Offset。明確的貢獻者專用聯合操作檢查兩個受影響輸出及拓樸；可在兩個輸出皆 OFF 時重新設定接收端波形，以解除其另一通道調變路由。Joint OFF 即使其中一個輸出失敗仍嘗試關閉兩個輸出，並確認兩個狀態。

組態設定使所選輸出保持 OFF。聯合設定完成時確認所有受影響輸出皆 OFF。聯合設定開始寫入後若發生失敗，會盡力關閉受影響通道並讀回確認，不解除拓樸。Preflight 拒絕及唯讀狀態查詢不會寫入輸出設定。開啟輸出仍須明確操作。工具不會在一般操作中使用 `*RST`。

## Software Sequence 範圍

Software Sequence 在三種已註冊型號皆支援規劃與模擬。Arbitrary 動作需要支援 Arbitrary 的型號。33512B 與 33521B 的 Product Live 接受符合既有波形、進階模式、觸發與主輸出範圍的 Sequence。包含 Arbitrary 動作的 Sequence 需在支援 Arbitrary 的型號上使用 Direct CLI 貢獻者驗證途徑。Worker Live Sequence 一律採用 Product 政策。

Basic Workflows 使用相同型號與通道限制。Amplitude Ramp 及其 output-on 振幅更新可用於 dry-run、模擬及 Direct CLI 貢獻者驗證。Product Live 及 Worker Live 在所有已註冊型號均拒絕 Amplitude Ramp。

## Adapter 功能介面

Direct CLI 提供由 Core 支援、較廣泛的波形、調變、Burst、Sweep、List Sweep、輸出、觸發與診斷介面，詳見 [CLI 指南](../cli/README.zh-TW.md)。

本機 Worker 在 dry-run 與模擬規劃使用 Core 已註冊型號，實機接收則使用 Core Product 政策。因此 33512B 與 33521B 在既有 Worker 命令介面內為 Product Live，而 33510B 仍僅支援無硬體模式。

Worker 型號接收不會擴大 Worker 命令介面。僅接受權威 [Worker 合約](../contracts/wavegen-worker-contract.md) 所列命令；不得由 Core 或 Direct CLI 命令可用性推斷 Worker 命令。
