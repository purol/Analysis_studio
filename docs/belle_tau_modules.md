# Belle_tau를 기준으로 확장한 GUI 모듈

`Belle_tau/analysis_code/src`의 C++ 파일 90개를 조사했습니다.
주석을 제외한 직접 Loader 호출은 35종이며 모두 GUI 블록으로 연결했습니다.
이는 메서드 수준의 지원입니다. C++ 제어 흐름이나 ROOT/RooStats 알고리즘 전체의 변환을 의미하지 않습니다.
[파일별 호출 목록](belle_tau_loader_coverage.json)은 다음 명령으로 재생성할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe tools/audit_belle_tau.py ../Belle_tau/analysis_code/src
```

## 카테고리

`Analysis tasks`는 제거했습니다. 기본 순서는 Loader → Input → Samples & weights → Selection →
Transform → Plot → BDT → Fit → Output → Advanced입니다.
Advanced는 처음에는 접혀 있으며, 기존 Load/Load With Cut/Cut/Draw TH1D/Define New Variable/BCS/Print ROOT File/Print Information과
Custom C++, C++ Support를 모았습니다. 기존 프로젝트의 블록 ID와 저장된 설정은 유지됩니다.

| GUI 블록 | 대응 Loader 메서드 / 사용 목적 |
|---|---|
| Samples | Load, LoadWithCut: 여러 디렉터리와 라벨 |
| Sample Roles | SetMC, SetData, SetSignal, SetBackground: Stack/BDT에 사용할 라벨 분류 |
| Event Weight | AddWeight: 가중치 이름과 입력 변수 매핑 |
| Cut Flow / Print Information | Cut, PrintInformation: 선택 단계 및 통계 출력 |
| Candidate Selection | BCS, RandomBCS, IsBCSValid: 최적/무작위 후보 선택 및 검증 |
| Event Split | RandomEventSelection: 이벤트 단위 분할 |
| Variables | DefineNewVariable, GetAverage, GetStdDev, GetDiff; 추가로 GetAdd, GetRandom |
| Ranked Variables | ConditionalPairDefineNewVariable: 운동량 순위 등에 따른 파생 변수 |
| Remove Variables | RemoveVariable |
| Plot Set | DrawTH1D: 수동/자동 binning, 정규화, log scale |
| Stack Plot Set | DrawStack: 여러 샘플을 쌓은 분포, 정규화, log scale |
| 2D Plot Set | DrawTH2D: X/Y 식과 범위, bin 수, ROOT draw 옵션 |
| Train FastBDT | FastBDTTrain: 변수 순서, 사전 선택, hyperparameters, balanced weights |
| Apply FastBDT | FastBDTApplication: classifier 경로와 출력 가지 |
| BDT Performance | CalculateAUC, DrawFOM, DrawPunziFOM |
| ROOT Output | PrintRootFile, PrintSeparateRootFile |
| Print Events | PrintEvent |
| ROOT Histogram | FillTH1D, FillCustomizedTH1D: ROOT 파일로 히스토그램 저장 |
| RooDataSet Output | FillDataSet: 관측량/표현식 매핑과 가중 데이터셋 저장 |
| ROOT Profile | FillTProfile: TProfile을 ROOT 파일로 저장 |
| Profile Fit | DefineAndFillProfile, DefineTF1, Fit, PlotFit, ExportFitResult, SaveWorkspace |
| Fit | 관측량·데이터셋·PDF 정의, 피팅, 플롯 및 ROOT 저장 |
| End | end: 모듈 실행 및 예약한 ROOT 객체 내보내기 |

## 사용 시 알아둘 설정

- 표의 Operation 셀은 선택 목록입니다. Variables의 여러 입력 식은 `a;b;c`처럼 세미콜론으로 나눕니다.
  함수 인자의 쉼표와 구분하기 위해 쉼표로 나누지 않습니다. 행 순서가 실행 순서입니다.
- Ranked Variables의 ranking/value 쌍과 출력 순위를 별도 표로 관리합니다.
  순위 0은 가장 높은 ranking 값입니다. 같은 ranking 식의 중복은 오류입니다.
- Event identity columns는 이벤트를 묶는 기준입니다. 기본값은 Loader의 기본 키와 같으며
  데이터의 이벤트 정의에 맞게 바꿀 수 있습니다. Event Split의 선택 인덱스는 0부터 시작합니다.
- Sample Roles는 로드한 샘플 라벨을 사용합니다. 같은 라벨을 MC와 Data 또는 Signal과 Background에 동시에 넣을 수 없습니다.
- FastBDT 적용 변수의 순서는 학습 순서와 같아야 합니다. 모델 파일 내부와의 일치 여부까지 자동 검증하지는 않습니다.
  Hyperparameter sweep은 프로그램별 설정이나 기존 Workflow For Each/Custom Command를 사용합니다.
  새로운 숫자 입력란이 자동으로 runtime argv에 바인딩되는 것은 아닙니다.
- Histogram/Dataset/Profile은 C++ 객체 수명을 Loader 실행까지 유지한 뒤 `end()` 다음에 저장합니다.
  여러 블록에는 서로 다른 출력 파일명을 지정하세요. 일반 ROOT 객체 저장은 RECREATE 모드입니다.
- Profile Fit의 TF1 파라미터 행 순서가 `[0]`, `[1]` 순서입니다. 복잡한 TF1 내장 함수의 파라미터 수는
  프레임워크에서 최종 검사합니다. 프로파일 피팅에는 RooFit SumW2/extended 옵션을 적용하지 않습니다.

## 사용자 정의 가중치·콜백

Event Weight의 C++ 입력은 **함수가 아니라 `EventWeight` 객체의 심볼**입니다.
예를 들어 헤더를 `code/MyObtainWeight.h`, 객체를 `MC_weight`, 등록 이름을 `MC_weight`로 지정하면
`EventWeights::Register("MC_weight", MC_weight)`와 `loader.AddWeight(...)`가 생성됩니다.
같은 객체의 동일 이름 등록은 프로그램당 한 번만 생성합니다. 서로 다른 객체를 같은 이름으로 등록하면 검증 오류입니다.
객체 심볼을 비우는 경우에는 앞선 C++ Support 등에서 이미 등록되어 있어야 합니다.

ROOT Histogram의 선택적 callback은 `double function(std::vector<double>)` 형태입니다.
사용할 입력 식은 한 줄에 하나씩 지정하고 함수의 헤더를 연결합니다.
가중치 교정표나 실제 분석 매핑 함수는 자동 생성하지 않습니다.

C++ Support에서는 프로젝트 상대 경로의 헤더와 추가 `.cc` 파일, 전역 정의,
현재 위치의 setup 문장, Loader 실행 후 문장을 각각 관리합니다.
헤더는 main 밖에 include하고 추가 소스는 컴파일에 전달합니다.
프로젝트 내부의 직접 지원 파일과 재귀적인 quoted include 변경은 빌드 freshness 검사에 반영됩니다.
외부 교정 데이터 파일과 시스템/프레임워크 헤더는 이 검사의 대상이 아닙니다.

## 예제와 아직 남은 범위

`examples/belle_tau_modules/belle_tau_modules.astudio.json`을 열면 세 프로그램이 있습니다.

- `selection_modules`: 가중치, 순위 변수, 통계 변수, cut, BCS, Stack/2D 플롯, ROOT 객체, profile fit, 이벤트 분할.
- `train_bdt`: 별도의 train 디렉터리로 모델 학습.
- `evaluate_bdt`: test 디렉터리로 적용 및 AUC/Punzi 출력. Workflow에서 학습 완료 후 실행합니다.

입력 파일은 포함하지 않습니다. tree/가지 이름과 경로는 실제 데이터에 맞춰야 합니다.
예제 가중치는 1.0으로 고정된 데모이고, MC 정규화나 muon-ID 교정을 재현하지 않습니다.
첫 프로그램의 분할 출력이 train/test 폴더를 자동 준비하는 것도 아닙니다.

Belle_tau의 CreateWorkSpace, Run_CLs, Asimov_fit, KS test, toy generation, 결과 병합,
resolution 파일을 읽어 cut을 구성하는 로직, 히스토그램 간 계산, RooHistPdf/HistFactory 작업은
직접 ROOT/RooStats 코드가 포함된 상위 분석입니다. 현재 Loader 모듈만으로 동일하게 표현할 수 없습니다.
해당 단계는 기존 Custom Command 또는 C++ Support로 연결해야 하며 자동 이식했다고 주장하지 않습니다.
복합/동시 PDF·NLL까지 포괄하는 FitManager 전체 GUI도 이 변경의 메서드 지원 범위와는 별개입니다.

검증: Python 모델/생성 코드/저장·복원/Qt 설정 화면 및 지원 파일 빌드 연결을 테스트했습니다.
ROOT 및 C++ 컴파일러가 없어 실제 C++ 컴파일, 데이터 입출력, 수치 적합 결과는 검증하지 않았습니다.
