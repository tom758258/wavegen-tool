[English](README.md)

# Wavegen Tool

Wavegen Tool 是以安全為優先的 Python 工具，透過 VISA 識別並控制明確選擇的波形產生器。單一發行套件提供可重複使用的 Core 套件、Direct CLI 與本機 Worker 控制介面。WebUI import package 已保留，但尚未提供可操作的使用者介面。

實機操作採 fail-closed（預設拒絕）：Core 檢查偵測到的儀器身分、所選 Resource、Backend、型號支援、通道及請求的操作。設定完成後所選輸出保持 OFF；啟用輸出一律需要明確命令。一般儀器控制不會重設儀器或自動選擇 VISA Resource。另有明確啟動的 Real Live Full 驗證 Suite，屬於下文說明的有界限重設例外。

## 功能特性

- 精確識別 Keysight/Agilent 型號及已註冊能力
- 無硬體的 dry-run SCPI 規劃與具狀態的記憶體內模擬器
- 靜態 sine（正弦波）、square（方波）、ramp（斜波）、triangle（三角波）、pulse（脈衝）、DC、noise（雜訊）及 PRBS 波形
- 受支援的 AM、FM、PM、FSK、BPSK、PWM 及 Sum 組態
- Counted Burst、Gated Burst、線性／對數掃頻及 List Sweep
- [Basic Workflows](docs/cli/README.zh-TW.md#basic-workflows)，提供定時輸出、振幅漸變、輸出循環、波形 Sequence、頻率步進及預設組態
- dry-run 與模擬中的具名任意波形 CSV 上傳、播放、原生 Sequence 及所選通道的揮發性記憶體管理
- 所選通道的狀態、輸出設定、輸出控制與有界限的錯誤佇列讀取
- 透過已允許的 VISA Backend 明確使用 USB 或 TCPIP/LAN Product Live
- 可供人員閱讀及 JSON 格式的 Direct CLI 輸出；僅限 loopback 的 Worker 生命週期及儀器命令
- 無硬體 Preflight 與另行核准的實機驗證

確切命令、參數、安全規則及實用範例請參閱 [CLI 指南](docs/cli/README.zh-TW.md)。

### 支援型號

| 型號 | 通道數 | Dry-run | 模擬器 | Product Live |
| --- | ---: | ---: | ---: | ---: |
| Keysight/Agilent 33510B | 2 | 是 | 是 | 否 |
| Keysight/Agilent 33512B | 2 | 是 | 是 | 是 |
| Keysight/Agilent 33521B | 1 | 是 | 是 | 是 |

33510B 為已註冊的無硬體 Profile，另有獨立的貢獻者驗證途徑；並非 Product Live。Product Live 接收不代表每種命令、Backend 或傳輸方式組合皆已經過獨立硬體驗證。型號、傳輸、Backend 與功能邊界以 [支援型號矩陣](docs/core/supported-models.zh-TW.md) 為準。

## 專案結構

```text
src/
  wavegen_tool_core/   Identity, capabilities, validation, SCPI, VISA, safety
  wavegen_tool_cli/    Direct CLI and local Worker
  wavegen_tool_webui/  Reserved WebUI package (not an operational UI)
docs/
  core/                Core and model support documentation
  cli/                 CLI and Worker usage
  contracts/           CLI JSON/JSONL and Worker/orchestrator contracts
scripts/               Hardware-free and opt-in live validation scripts
tests/                 Hardware-free Core, CLI, Worker, and script tests
```

Core 不匯入 CLI 或 WebUI。Adapter 透過 Core 取得儀器命令、型號專屬決策、能力與安全檢查。

## 主要依賴

- **Python 3.10+**：應用程式與貢獻者命令
- **PyVISA** (`pyvisa`)：必要的 Python 依賴套件
- **System VISA 執行環境**（例如 Keysight IO Libraries Suite）：供文件中的預設 `--backend system` 實機途徑使用，不隨套件提供
- **PyVISA-py** (`pyvisa-py`)：選用套件，使用 `--backend @py` TCPIP/LAN 時須另行安裝
- **uv**：用於文件說明的貢獻者工作流程；**pytest**、**Ruff** 與 **build** 由專案開發依賴提供

根目錄的 [pyproject.toml](pyproject.toml) 是唯一的發行套件及 console entry point 定義。

## 安裝

從專案根目錄安裝已鎖定版本的貢獻者環境：

```powershell
uv sync --all-extras --locked --link-mode=copy
uv run wavegen-tool --help
```

若使用選用的 `@py` Backend，請在同步**之後**安裝 `pyvisa-py`：

```powershell
uv pip install pyvisa-py
```

後續的 `uv sync` 可能移除手動安裝的套件。預設 `--backend system` 使用 PyVISA 的執行環境解析機制，不保證特定底層程式庫，也不會 fallback 至其他 Backend。安裝 Backend 不會擴大 Product Live 範圍：`system` 接受 USB 與 TCPIP/LAN，`@py` 僅接受 TCPIP/LAN，`@bt` 沒有 Product Live 範圍。參閱 [CLI 安裝與安全](docs/cli/README.zh-TW.md#installation-and-invocation)。

## 快速開始

不接觸硬體，檢查已註冊 Profile：

```powershell
uv run wavegen-tool capabilities --model keysight-33521b --json
```

不執行 VISA I/O，預覽 sine 波形設定：

```powershell
uv run wavegen-tool configure-sine `
  --dry-run `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1
```

在記憶體內模擬器執行相同組態：

```powershell
uv run wavegen-tool configure-sine `
  --simulate `
  --model keysight-33521b `
  --frequency-hz 1000 `
  --amplitude-vpp 0.1
```

安裝 VISA 執行環境後，明確列出 Backend 回報的 Resource，而不開啟儀器工作階段：

```powershell
uv run wavegen-tool list-resources --backend system
```

選用的 `--live-only` 探索模式**確實會**開啟符合條件的 Resource 並查詢 `*IDN?`。列出 Resource 或取得 IDN 回應皆不會自動授予 Product Live 控制權限。參閱 [Resource 列出](docs/cli/README.zh-TW.md#visa-resource-listing)。

dry-run 與模擬不使用硬體，不等於實機驗證。Direct CLI Live 需要操作人員提供 `--resource` 或由操作人員設定 `WAVEGEN_TOOL_RESOURCE`；Worker Live 及實機驗證腳本需要明確的 Resource 引數。訊號設定後保持 OFF，直到另行執行 `output --state on`。

## 建置

建置既有的 Python wheel 與原始碼發行套件（不是獨立 Windows 執行檔或 Desktop 應用程式）：

```powershell
uv run python -m build
```

產物寫入 `dist/`。使用根目錄 `pyproject.toml` 及其 `setuptools` 建置 Backend，不另行定義元件套件。

## 測試

執行無硬體專案測試與原始碼 lint 檢查：

```powershell
uv run python -m pytest -q -rs -p no:cacheprovider --basetemp .tmp_tests/readme-pytest
uv run python -m ruff check src tests
```

變更時應選擇的重點檢查請參閱 [Testing Guidelines](docs/testing-guidelines.md)。Windows 貢獻者亦可執行無硬體 CLI Preflight：

```powershell
.\scripts\preflight-cli.ps1 -Target all
```

另行啟動的 Real Live Full Suite **不是**預設測試。作為特定的驗證例外，它僅在確認所有輸出 OFF 後，於每個獨立案例前執行有界限的 `*RST`，接著檢查完成狀態與安全 OFF 狀態。一般 CLI、Worker、dry-run、模擬器及 Preflight 途徑不執行此重設。實機驗證需要操作人員明確提供 Resource，並遵循 [Contributing](docs/CONTRIBUTING.md) 的程序。

## 文件

- [Core 概覽](docs/core/README.zh-TW.md)
- [Core 整合指南](docs/core/integration.md)
- [支援型號](docs/core/supported-models.zh-TW.md)
- [CLI 指南與範例](docs/cli/README.zh-TW.md)
- [貢獻與驗證工作流程](docs/CONTRIBUTING.md)
- [Testing Guidelines](docs/testing-guidelines.md)
- [Wavegen CLI JSON / JSONL 合約](docs/contracts/wavegen-cli-jsonl-contract.md)
- [Wavegen Worker 合約](docs/contracts/wavegen-worker-contract.md)
- [Wavegen Orchestrator 工作流程](docs/contracts/wavegen-orchestrator-workflows.md)
- [Common CLI JSONL 合約](docs/contracts/common-cli-jsonl-contract.md)
- [Common Worker Protocol](docs/contracts/common-worker-protocol.md)
- [Common Orchestrator 工作流程](docs/contracts/common-orchestrator-workflows.md)

## 貢獻

請先閱讀 [CONTRIBUTING.md](docs/CONTRIBUTING.md)。一般變更採用重點無硬體檢查。涉及實機的變更需另行明確同意驗證，並依規定處理私人驗證證據。

## 授權條款與免責聲明

Wavegen Tool 採用 [MIT License](LICENSE)。這是儀器控制工具，不能取代對儀器設定、連接負載、外部觸發或實驗台安全的確認。操作人員必須在明確啟用輸出前檢視其設定。無硬體結果不得被表述為實體儀器驗證。
