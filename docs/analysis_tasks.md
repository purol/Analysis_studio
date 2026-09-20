# Analysis tasks 사용 안내

새 Loader Program을 만들면 `Loader Declaration → Samples → Cut Flow → End`가 연결된 상태로 시작합니다.
첫 Cut Flow 조건은 `1`(모든 이벤트 통과)입니다. 실제 분석 조건으로 바꾸세요.
기존 프로젝트의 블록과 C++ 생성 방식은 유지됩니다.

## 입력과 선택

- **Loader Declaration**: tree 이름을 설정합니다. 클래스는 `Loader`로 고정되며 선택 입력은 없습니다.
  새 Loader의 변수명은 같은 프로그램에서 중복되지 않게 `loader`, `loader_2` 등으로 지정됩니다.
  `Show advanced C++ settings`는 저장된 변수명을 표시/숨기기만 합니다. 체크 여부는 코드 생성에 영향을 주지 않습니다.
  수동 이름은 체크를 해제해도 유지됩니다. 수동 변경으로 이름이 겹치면 검증 오류가 발생합니다.
  서로 다른 Loader Program은 별도 C++ 실행 파일이므로 같은 변수명을 사용해도 됩니다.
- **Samples**: 경로, 파일명에 포함될 문자열, 샘플 라벨, 선택적 초기 cut을 표에 입력합니다.
  C++ 따옴표는 필요 없습니다. `Directory argv`가 0이면 경로를 쓰고, 1 이상이면 실행 인자를 씁니다.
  Workflow의 Loader Execute `argv`에 한 줄씩 인자를 설정하세요. 필터는 glob이 아닌 부분 문자열입니다.
- **Cut Flow**: 각 행이 하나의 선택 단계입니다. 위에서 아래로 cut을 적용하고, 선택한 행에서
  `PrintInformation`을 호출합니다. Use를 끄면 해당 cut과 진단 출력을 모두 생략합니다.
  공통 플롯 목록을 모든 활성 단계의 전/후/양쪽에 적용할 수 있습니다.
  단계별 출력 이름에 블록 ID, 행 번호, before/after가 들어가 덮어쓰기를 방지합니다.
- **Plot Set**: 현재 위치의 데이터에 여러 분포를 한 번에 그립니다.
  파일명과 출력 폴더를 분리해서 입력합니다. 폴더는 생성된 프로그램이 만듭니다.

표는 Add / Duplicate / Remove / ↑ / ↓로 편집합니다.
`Open large table editor…`로 넓게 편집하고 OK로 적용하거나 Cancel로 취소할 수 있습니다.
캔버스에는 활성 행 수가 표시되고, 일반 Undo/Redo 및 프로젝트 저장에 표 내용도 포함됩니다.
새 Fit이나 Plot Set을 넣을 때는 기존 연결을 지우고 실행할 순서로 연결하세요.

## Fit

Fit 블록은 그 위치에서 관측량 정의, 데이터셋 채우기, 파라미터/PDF 정의,
피팅, 결과 플롯 및 선택적 ROOT 저장을 구성합니다.
Gaussian, BifurGauss, Exponential, BreitWigner를 지원합니다.

1. 관측량 식과 데이터 범위를 입력합니다.
2. PDF를 고르고 `Replace parameters with model preset`을 누릅니다.
   이 버튼은 현재 파라미터 표를 교체합니다. PDF 선택만으로 기존 값이 삭제되지는 않습니다.
3. 파라미터 순서, 초기값, 범위를 분석에 맞게 조정합니다. 프리셋 수치는 질량 분석용 시작 예시이며 최적값이 아닙니다.
4. 필요하면 `Fit a restricted range`를 켜서 peak 구간만 피팅합니다.
5. PNG, 결과 ROOT, workspace 경로를 정합니다. 빈 출력 경로는 생략합니다.
   여러 Fit 블록의 출력에는 서로 다른 파일명을 사용하세요.

파라미터는 PDF 인자 순서로 전달됩니다. 내부 객체 ID는 블록별로 생성되므로
서로 다른 Fit 블록에서 mean, sigma라는 이름을 재사용할 수 있습니다.
데이터셋은 Loader가 계산한 이벤트 가중치를 사용합니다. SumW2 옵션은 오차 계산 옵션이며
가중치 자체를 등록하는 기능은 아닙니다.

코드 생성은 공개 Loader API를 사용합니다. `DefineAndFillDataSet`, `Fit`, `PlotFit` 등을
순서대로 예약하고 마지막 `end()`가 프레임워크의 단계 실행을 수행합니다.
`Fit` 모듈은 `End()`에서 실행되고 downstream barrier를 제공하므로 GUI가 private FitManager를 직접 호출하지 않습니다.

## 예제 및 검증 범위

`examples/analysis_tasks/tau_selection.astudio.json`을 Open으로 열어 보세요.
Belle_tau의 질량/에너지 선택과 BifurGauss 사용 방식을 보여 주는 간단한 예제입니다.
실제 입력 디렉터리와 tree, 가지 이름을 확인해야 합니다. MC 가중치 등록과 모든 production cut을 복제한 분석은 아닙니다.
생성된 C++도 같은 디렉터리에 제공됩니다.

숫자 범위, bin 수, 필수 식, 파라미터 수와 초기값, 표 내부 파일명 충돌을 코드 생성 전에 검사합니다.
이 컴퓨터에서는 Qt 편집 동작, 저장/복원, 생성 코드 순서를 테스트했습니다.
ROOT와 C++ 컴파일러가 없어 실제 ROOT 컴파일·수치 피팅은 검증하지 않았습니다.

Loader 내부에서 처리되는 Profile Fit과 BDT 학습/적용은 전용 블록으로 지원합니다.
Variable Optimization은 일반 변수와 BDT 출력 모두의 FOM/Punzi/AUC 평가에 사용합니다.
외부 객체·심볼 연결을 요구하는 블록은 GUI에서 제외합니다.
새 카테고리와 전체 대응 목록은 [Belle_tau 모듈 가이드](belle_tau_modules.md)를 참고하세요.
복합/동시 PDF, NLL, CLs 및 직접 ROOT 후처리 전체를 GUI화한 것은 아닙니다.

함께 수정한 프레임워크 파일: `Belle2_analysis/include/fit_manager.h`의
`ExportFitResult`에서 `structureDetermined`와 `structure_determined`가 섞인 컴파일 오류를 바로잡았습니다.
이는 서브모듈 내부 변경이므로 저장소에 반영할 때 서브모듈 변경도 별도로 관리해야 합니다.
