# Loader 모듈 GUI 사용 안내

GUI에서는 Loader의 기능으로 구성되는 작업을 제공합니다.
외부 C++ 객체·가중치 객체·콜백·포인터를 사용자가 연결하는 방식은 제공하지 않습니다.

## 카테고리와 색상

Loader → Input → Selection → Transform → Plot → Optimization → BDT → Fit → Output → Advanced 순서입니다.
기존 팔레트에 맞춰 입력은 파랑, 선택/최적화는 주황, 변수 변환은 초록,
플롯/출력은 장밋빛, BDT/Fit은 보라 계열로 표시합니다. Advanced는 처음에는 접혀 있습니다.

| 카테고리 | 주요 블록 |
|---|---|
| Input | Samples, Sample Roles |
| Selection | Cut Flow, Candidate Selection, Event Split |
| Transform | Variables, Ranked Variables, Remove Variables |
| Plot | Plot Set, Stack Plot Set, 2D Plot Set |
| Optimization | Variable Optimization |
| BDT | Train FastBDT, Apply FastBDT |
| Fit | Fit, Profile Fit |
| Output | ROOT Output, Print Events |
| Advanced | 기존 Load/Cut/Draw TH1D 등 개별 호출 및 Custom C++ |

## FastBDT와 일반 변수 최적화

Train FastBDT의 Hyperparameter 열에는 항상 보이는 드롭다운이 있습니다.
NTrees, Depth, Shrinkage, Subsample, Binning 중 선택합니다.
값은 옆 칸에 입력합니다. 기본 다섯 행이 제공되며 중복 이름과 범위를 검사합니다.
Apply FastBDT의 입력 변수 순서는 학습 순서와 같아야 합니다.

기존 BDT Performance는 **Variable Optimization**으로 변경했습니다.
분석 변수나 표현식을 입력하고 Sample Roles에서 Signal/Background를 지정하세요.
BDT 출력 외에도 thrust, 운동량, 질량 등의 변수에 사용할 수 있습니다. 스캔 범위는 변수에 맞게 조정합니다.

| Metric | 생성 결과 | 자동 확장자 |
|---|---|---|
| FOM | 변수의 cut을 스캔한 FOM 그래프 | `.png` |
| Punzi | 변수의 cut을 스캔한 Punzi FOM 그래프 | `.png` |
| AUC | 수치 AUC 결과를 기록한 텍스트 파일 | `.txt` |

**Output folder**와 **File name**만 입력합니다. 확장자는 지표에 맞춰 자동으로 붙으며,
설정 화면의 Output file에 실제 저장 경로가 표시됩니다.
예를 들어 폴더 `results`, 파일명 `thrust_scan`이면 Punzi는 `results/thrust_scan.png`,
AUC는 `results/thrust_scan.txt`입니다. 파일명에 `.png`나 `.txt`를 입력해도 중복으로 붙이지 않습니다.
AUC write mode의 `w`는 덮어쓰기, `a`는 기존 텍스트에 추가하기입니다.
지표를 바꾸면 같은 폴더와 이름에 새 형식을 적용합니다.

## 외부 객체 블록과 이전 프로젝트

ROOT Histogram, RooDataSet Output, ROOT Profile, Event Weight, C++ Support는
팔레트에서 제거했으며 외부 객체 연결 편집 화면도 제공하지 않습니다.
기존 프로젝트에 저장된 블록은 삭제하거나 다른 동작으로 바꾸지 않고 보존합니다.
선택하면 제외 사유가 표시됩니다. 기존 파일을 읽고 코드를 재생성하기 위한 호환 스키마는 남아 있습니다.
새 예제에는 이 블록들이 포함되지 않습니다.

**Fit과 Profile Fit은 유지합니다.** 이들은 외부 RooDataSet/TF1 포인터를 연결하지 않고,
Loader의 DefineAndFillDataSet/DefineAndFillProfile/DefineModel/DefineTF1/Fit API로 구성됩니다.
가중치 교정표·사용자 콜백·ROOT/RooStats를 직접 사용하는 분석은 현재의 GUI 작업 범위 밖입니다.

이전 BDT Performance 블록은 열 때 Variable Optimization으로 표시하고,
기존 출력 경로를 폴더와 파일명으로 옮깁니다. 확장자는 현재 지표에 맞춰 결정합니다.
사용자가 지정한 블록 이름과 변수 식은 보존합니다.

## 예제와 조사 범위

`examples/belle_tau_modules/belle_tau_modules.astudio.json`에는 세 프로그램이 있습니다.

- `selection_modules`: 순위/통계 변수, 선택, BCS, Stack/2D 플롯, Profile Fit, 이벤트 분할 및 ROOT 출력.
- `train_bdt`: train 디렉터리로 학습.
- `evaluate_bdt`: test 디렉터리로 적용 및 Variable Optimization으로 AUC/Punzi 출력.

입력 파일은 포함하지 않습니다. tree/가지 이름과 경로를 실제 데이터에 맞추세요.
예제는 MC 정규화나 muon-ID 교정을 적용하지 않습니다. 분할 출력이 train/test 폴더를 자동 준비하지도 않습니다.

Belle_tau의 C++ 파일 90개에서 직접 Loader 호출 35종을 조사했습니다.
[파일별 목록](belle_tau_loader_coverage.json)의 `gui_available`이 현재 GUI 제공 여부를 나타냅니다.
이전의 모든 메서드가 GUI에 있다는 설명은 외부 객체 방식 제외 이후에는 적용되지 않습니다.

```powershell
.\.venv\Scripts\python.exe tools/audit_belle_tau.py ../Belle_tau/analysis_code/src
```

CLs, HistFactory, toy 생성, 직접 ROOT 히스토그램 계산 등 분석 전체의 자동 이식을 지원하는 것은 아닙니다.
모델/코드 생성/저장·복원/Qt 화면을 테스트했습니다. ROOT와 C++ 컴파일러가 없는 현재 환경에서는
실제 C++ 컴파일과 데이터 분석 결과를 검증하지 않았습니다.
