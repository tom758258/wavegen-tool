# Wavegen Tool Core

`wavegen_tool_core` 是 Wavegen Tool 面向儀器行為的權威來源，負責型號身分識別、能力、請求接收、命令規劃、VISA/Backend 行為、模擬及硬體安全。CLI 與保留的 WebUI 套件是 Core 的 Adapter，不得另行實作平行的身分識別、能力、SCPI、VISA 或安全機制。

## 用途與權責

Core 負責：

- 精確解析實機身分與已註冊型號；
- 型號能力，包括通道數與頻率限制；
- 請求與組態驗證；
- 靜態波形、調變、Burst、Sweep、List Sweep、輸出、狀態、觸發及錯誤佇列行為；
- 所選通道、耦合、追蹤與輸出安全規則；
- Resource 傳輸方式及 Backend 正規化；
- 實機 VISA 工作階段執行與清理；
- 無硬體的 dry-run 規劃及模擬器行為。

Core 是支援範圍、能力、實機接收與儀器安全的最終權威。Adapter 進行的驗證無法授權 Core 已拒絕的請求。

## Adapter 邊界

相依方向如下：

```text
CLI   -> Core
WebUI -> Core
```

Core 不得匯入 `wavegen_tool_cli` 或 `wavegen_tool_webui`。Adapter 負責輸入解析、呈現、序列化及各自的生命週期行為。Core 負責面向儀器的行為，並回傳結構化結果或錯誤。

WebUI import package 目前為保留用途，尚未定義公開的 WebUI 功能介面。

## 身分識別與型號登錄

Core 的廠商 Registry 定義穩定的 vendor ID、顯示名稱、標準製造商名稱與製造商別名。已註冊廠商 `keysight` 的顯示名稱為 `Keysight`，標準製造商名稱為 `Keysight Technologies`，別名為 `Agilent Technologies`。每筆型號記錄將 model ID、標準型號名稱及顯示名稱對應至已註冊的廠商。Keysight 有三筆型號記錄。

- `keysight-33510b` (`33510B`)
- `keysight-33512b` (`33512B`)
- `keysight-33521b` (`33521B`)

實機識別及資源探索採用相同的 Core 查詢機制。Core 先將 `*IDN?` 回報的製造商解析至廠商，再於該廠商內比對回報的型號。製造商名稱經大小寫與空白正規化後精確比對，包含已註冊製造商別名；型號則忽略大小寫與前後空白後精確比對。Core 拒絕重複或衝突的 Registry 記錄，未知、格式不正確、不支援或不相符的實機身分均採 fail-closed（預設拒絕）。資源探索僅回報型號分類；實機控制仍須符合適用的 Core 支援政策。

dry-run 或模擬器可明確選擇已註冊的規劃用型號，但該選擇不能建立實體儀器的身分。一般實機操作使用精確偵測的身分；預期型號只能作為檢查條件，不能覆寫結果。

## 能力與所選通道

能力為 Core Registry 管理的資料：

| 型號 | 通道數 | Sine/Square/Pulse/Noise 共用頻率上限 |
| --- | ---: | ---: |
| 33510B | 2 | 20 MHz |
| 33512B | 2 | 20 MHz |
| 33521B | 1 | 30 MHz |

每項所選通道請求皆依解析後的 Profile 進行檢查。單通道 33521B 拒絕 Channel 2。

對雙通道實機，獨立的狀態變更操作會採 fail-closed，除非頻率耦合、電壓耦合、Channel 1 追蹤及 Channel 2 追蹤皆回報為 OFF。Core 不會自動停用耦合或追蹤。唯讀狀態查詢不需要此獨立通道防護。

波形及輸出設定只關閉所選通道的輸出，並保持 OFF。開啟輸出是另一項明確操作。

## 明確的雙通道操作

`run_dual_channel(action, arguments, model=...)` 規劃命令；`run_dual_channel(action, arguments, resource=..., backend=...)` 對已允許的模擬器或精確符合貢獻者驗證範圍的型號執行。
[CLI 指南](../cli/README.zh-TW.md#dual-channel-operations) 說明命令，[Worker 合約](../contracts/wavegen-worker-contract.md#dual-channel-arguments) 定義結構化引數。獨立寫入不會繞過一般的獨立通道防護。耦合更新要求兩個輸出皆為 OFF。Group OFF 確認兩個輸出皆為 OFF，不改變拓樸。Software Sequence 使用相同操作，並在清理時確認受影響的輸出皆為 OFF，不會擅自停用耦合、追蹤或 Combine。

## Dry-run、模擬器與實機模式

dry-run 驗證已註冊的規劃用型號、所選通道、請求的組態及安全限制，再回傳依序排列的 SCPI 計畫。它不會建立 VISA Resource Manager、開啟 Resource、查詢 `*IDN?`、傳送 SCPI 或變更硬體。

模擬器提供具確定性、程序內的無硬體狀態。獨立 CLI 支援已註冊 Profile，並為雙通道型號維持獨立通道狀態。新的獨立 CLI 程序從全新狀態開始；直接呼叫端可重複使用模擬器模組提供的物件以保留狀態。模擬器行為不等於硬體驗證。

實機操作會在開啟唯一明確指定的 Resource 前，驗證傳輸方式與 Backend。Core 解析精確身分，並在執行該操作專屬的實機 I/O 前套用適用的支援與安全檢查。不允許型號覆寫或 Backend fallback。

## VISA 與 Backend 邊界

直接識別與控制操作僅接受 USB 及 TCPIP/LAN Resource：

| 選擇器 | Core Backend 身分 | 實機傳輸範圍 |
| --- | --- | --- |
| 省略或 `system` | `system_visa` | USB 與 TCPIP/LAN |
| `@py` | `pyvisa_py` | 僅 TCPIP/LAN |
| `@bt` | `pyvisa_bt` | 無 Product Live 或實機驗證開放範圍 |

安裝 VISA 執行環境並不授予型號或連線支援。資源探索是獨立且明確的診斷操作，不會擴大識別／控制的邊界。

## 輸出安全與 Fail-closed 行為

Core 拒絕缺少 metadata、不支援的傳輸方式或 Backend 組合、格式不正確或不支援的身分、預期型號不符、不可用的通道、無效的參數組合、不安全的耦合／追蹤狀態及驗證失敗。一般操作不使用 `*RST`、preset 或 setup recall。

識別及唯讀診斷不會開啟輸出。組態設定不會隱含開啟輸出。整台儀器的觸發操作傳送一次 `*TRG`，並非所選通道操作。

## 公開套件介面

受支援的頂層 import 邊界為 `wavegen_tool_core`，提供組態與結果型別、結構化錯誤、身分及 Backend 輔助功能、模擬器基本元件，以及識別、資源列出、狀態、錯誤佇列讀取、波形設定、掃頻、輸出及 Bus 觸發的 Core 操作。

部分型號 Registry、能力及多 Profile 模擬器的輔助功能保留在各自的 Core 子模組。下游 Adapter 應優先使用套件根目錄已匯出的操作，不得匯入底線開頭的實作輔助功能或直接組合 SCPI。

## 測試

Core 測試預設不使用硬體。使用 dry-run、模擬器或 fake VISA 工作階段涵蓋規劃、驗證、路由、安全及錯誤路徑。參閱專案的 [Testing Guidelines](../testing-guidelines.md)。

## 相關文件

- [Core 整合](integration.md)
- [支援型號](supported-models.zh-TW.md)
- [CLI 指南](../cli/README.zh-TW.md)
- [Testing Guidelines](../testing-guidelines.md)
- [Worker 合約](../contracts/wavegen-worker-contract.md)
