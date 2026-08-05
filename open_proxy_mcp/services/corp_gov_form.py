"""기업지배구조보고서 KRX 서식 레지스트리 — 섹션 코드와 표 번호 체계.

원문은 서식 자체가 두 층의 번호를 달고 나온다.

1. **섹션 코드** `[NNNNNN]` — 본문 각 절의 머리에 붙는다.
   `00000N` 은 문서 골격(신고서·기업개요·핵심지표·기업지배구조 현황) 4종이고,
   나머지는 `[장1][핵심원칙2][세부원칙1][00]` 으로 장 5 + 핵심원칙 10 + 세부원칙 28 = 43종이다.
   예) `201100` = 2장(주주) · 핵심원칙 1 · 세부원칙 1-1.

2. **표 번호** `표 X-Y-Z` — 서식이 정의한 표에만 붙고, 표 몸통은
   `<table-group aclass="krx-cg_...">` 로 개념 코드를 함께 단다.
   `aclass` 가 없는 표는 회사가 자유편집으로 덧붙인 것이라 서식 표가 아니다.
   위원회 개최 내역은 서식이 세 칸(이사후보추천·리스크관리·내부거래)만 정의하므로,
   감사위원회·ESG·보상위원회 등이 `표 8-2-4` 이후에 나와도 번호가 위원회를 특정하지 못한다.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import html as lxml_html


SECTION_CODES: dict[str, str] = {
    '000001': '기업지배구조보고서 신고서',
    '000002': 'I. 기업개요',
    '000003': '지배구조핵심지표 준수 현황',
    '000004': 'II. 기업지배구조 현황',
    '100000': '1. 기업지배구조 일반정책',
    '200000': '2. 주주',
    '201000': '(핵심원칙1) 주주는 권리행사에 필요한 충분한 정보를 시의적절하게 제공받고, 적절한 절차에 의해 자신의 권리를 행사할 수 있어야 한다.',
    '201100': '(세부원칙 1-1) - 기업은 주주에게 주주총회의 일시, 장소 및 의안 등에 관한 충분한 정보를 충분한 기간 전에 제공하여야 한다.',
    '201200': '(세부원칙 1-2) - 주주총회에 주주가 최대한 참여하여 의견을 개진할 수 있도록 하여야 한다.',
    '201300': '(세부원칙 1-3) - 기업은 주주가 주주총회의 의안을 용이하게 제안할 수 있게 하여야 하며, 주주총회에서 주주제안 의안에 대하여 자유롭게 질의하고 설명을 요구할 수 있도록 하여야 한다.',
    '201400': '(세부원칙 1-4) - 기업은 배당을 포함한 중장기 주주환원정책 및 향후 계획 등을 마련하고 주주들에게 배당관련 예측가능성을 제공하여야 한다.',
    '201500': '(세부원칙 1-5) - 주주환원정책 및 향후 계획 등에 근거하여 적절한 수준의 배당 등을 받을 주주의 권리는 존중되어야 한다.',
    '202000': '(핵심원칙 2) 주주는 보유주식의 종류 및 수에 따라 공평한 의결권을 부여받아야 하고, 주주에게 기업정보를 공평하게 제공하는 시스템을 갖추는 노력을 해야 한다.',
    '202100': '(세부원칙 2-1) - 기업은 주주의 의결권이 침해되지 않도록 하여야 하며, 주주에게 기업정보를 적시에, 충분히 그리고 공평하게 제공하여야 한다.',
    '202200': '(세부원칙 2-2) - 기업은 지배주주 등 다른 주주의 부당한 내부거래 및 자기거래로부터 주주를 보호하기 위한 장치를 마련·운영하여야 한다.',
    '202300': '(세부원칙 2-3) - 기업은 주주간 이해관계를 달리하는 기업의 소유구조 또는 주요 사업의 변동 및 자본조달정책에 있어 주주에게 충분히 설명하고 소액주주 의견수렴, 반대주주 권리보호 등 주주보호 방안을 강구하여야 한',
    '300000': '3. 이사회',
    '303000': '(핵심원칙 3) 이사회는 기업과 주주이익을 위하여 기업의 경영목표와 전략을 결정하고, 경영진을 효과적으로 감독하여야 한다.',
    '303100': '(세부원칙 3-1) - 이사회는 경영의사결정 기능과 경영감독 기능을 효과적으로 수행하여야 한다.',
    '303200': '(세부원칙 3-2) - 이사회는 최고경영자 승계정책을 마련하여 운영하고, 지속적으로 개선·보완하여야 한다.',
    '303300': '(세부원칙 3-3) - 이사회는 회사의 위험을 적절히 관리할 수 있도록 내부통제정책을 마련하여 운영하고, 지속적으로 개선·보완하여야 한다.',
    '304000': '(핵심원칙 4) 이사회는 효율적으로 의사를 결정하고 경영진을 감독할 수 있도록 구성하여야 하며, 이사는 다양한 주주의견을 폭넓게 반영할 수 있는 투명한 절차를 통하여 선임되어야 한다.',
    '304100': '(세부원칙 4-1) - 이사회는 회사의 지속가능한 발전을 위한 중요 사항에 대하여 효과적이고 신중한 토의 및 의사결정이 가능하도록 구성하여야 하며, 경영진과 지배주주로부터 독립적으로 기능을 수행할 수 있도록 충분한 ',
    '304200': '(세부원칙 4-2) - 이사회는 기업경영에 실질적으로 기여할 수 있도록 지식 및 경력 등에 있어 다양한 분야의 전문성 및 책임성을 지닌 유능한 자로 구성하여야 한다.',
    '304300': '(세부원칙 4-3) - 이사 후보 추천 및 선임과정에서 공정성과 독립성이 확보되도록 하여야 한다.',
    '304400': '(세부원칙 4-4) - 기업가치의 훼손 또는 주주권익의 침해에 책임이 있는 자를 임원으로 선임하지 않도록 노력하여야 한다.',
    '305000': '(핵심원칙 5) 사외이사는 독립적으로 중요한 기업경영정책의 결정에 참여하고 이사회의 구성원으로서 경영진을 감독ㆍ지원할 수 있어야 한다.',
    '305100': '(세부원칙 5-1) - 사외이사는 해당 기업과 중대한 이해관계가 없어야 하며, 기업은 선임단계에서 이해관계 여부를 확인하여야 한다.',
    '305200': '(세부원칙 5-2) - 사외이사는 충실한 직무수행을 위하여 충분한 시간과 노력을 투입하여야 한다.',
    '305300': '(세부원칙 5-3) - 기업은 사외이사의 직무수행에 필요한 정보, 자원 등을 충분히 제공하여야 한다.',
    '306000': '(핵심원칙 6) 사외이사의 적극적인 직무수행을 유도하기 위하여 이들의 활동내용은 공정하게 평가되어야 하고, 그 결과에 따라 보수지급 및 재선임 여부가 결정되어야 한다.',
    '306100': '(세부원칙 6-1) - 사외이사의 평가는 개별실적에 근거하여 이루어져야 하고, 평가결과는 재선임 결정에 반영되어야 한다.',
    '306200': '(세부원칙 6-2) - 사외이사의 보수는 평가 결과, 직무수행의 책임과 위험성 등을 고려하여 적정한 수준에서 결정되어야 한다.',
    '307000': '(핵심원칙 7) 이사회는 기업과 주주의 이익을 위한 최선의 경영의사를 결정할 수 있도록 효율적이고 합리적으로 운영되어야 한다.',
    '307100': '(세부원칙 7-1) - 이사회는 원칙적으로 정기적으로 개최되어야 하며, 이사회의 권한과 책임, 운영절차 등을 구체적으로 규정한 이사회 운영규정을 마련하여야 한다.',
    '307200': '(세부원칙 7-2) - 이사회는 매 회의마다 의사록을 상세하게 작성하고, 개별이사의 이사회 출석률과 안건에 대한 찬반여부 등 활동내역을 공개하여야 한다.',
    '308000': '(핵심원칙 8) 이사회는 효율적인 운영을 위하여 그 내부에 특정 기능과 역할을 수행하는 위원회를 설치하여야 한다.',
    '308100': '(세부원칙 8-1) - 이사회 내 위원회는 과반수를 사외이사로 구성하되 감사위원회와 보상(보수)위원회는 전원 사외이사로 구성하여야 한다.',
    '308200': '(세부원칙 8-2) - 모든 위원회의 조직, 운영 및 권한에 대하여는 명문으로 규정하여야 하며, 위원회는 결의한 사항을 이사회에 보고하여야 한다.',
    '400000': '4. 감사기구',
    '409000': '(핵심원칙 9) 감사위원회, 감사 등 내부감사기구는 경영진 및 지배주주로부터 독립적인 입장에서 성실하게 감사 업무를 수행하여야 하며, 내부감사기구의 주요 활동내역은 공시되어야 한다.',
    '409100': '(세부원칙 9-1) - 감사위원회, 감사 등 내부감사기구는 독립성과 전문성을 확보하여야 한다.',
    '409200': '(세부원칙 9-2) - 감사위원회, 감사 등 내부감사기구는 정기적 회의 개최 등 감사 관련 업무를 성실하게 수행하고 활동 내역을 투명하게 공개해야 한다.',
    '410000': '(핵심원칙 10) 기업의 회계정보가 주주 등 그 이용자들로부터 신뢰를 받을 수 있도록 외부감사인은 감사대상기업과 그 경영진 및 지배주주 등으로부터 독립적인 입장에서 공정하게 감사업무를 수행하여야 한다.',
    '410100': '(세부원칙 10-1) - 내부감사기구는 외부감사인 선임시 독립성, 전문성을 확보하기 위한 정책을 마련하여 운영하여야 한다.',
    '410200': '(세부원칙 10-2) - 내부감사기구는 외부감사 실시 및 감사결과 보고 등 모든 단계에서 외부감사인과 주기적으로 의사소통하여야 한다.',
    '500000': '5. 기타사항',
}


#: 표 번호 → 서식 명세. `axis="row"` 는 항목이 행에, `"col"` 은 열에 놓인 표다.
#: `labels` 는 그 축의 머리글이며 `#key-indicators` 는 표 번호가 없는 15개 핵심지표 표다.
FORM_TABLES: dict[str, dict[str, Any]] = {
    '#key-indicators': {
        "aclass": 'krx-cg_ComplianceStatusWithKeyIndicatorsOfCorporateGovernanceAbstract',
        "title_ko": '지배구조핵심지표 준수 현황',
        "title_en": '5. Compliance with Key Governance Indicators',
        "axis": 'col',
        "labels": ['핵심지표', '(공시대상기간)준수여부', '(직전 공시대상기간)준수여부', '비고'],
    },
    '1-1-1': {
        "aclass": 'krx-cg_InformationOfTheGeneralMeetingOfShareholdersAbstract',
        "title_ko": '주주총회 개최 정보',
        "title_en": "Information on the General Shareholders' Meetings",
        "axis": 'row',
        "labels": ['', '정기 주총 여부', '소집결의일', '소집공고일', '주주총회개최일', '공고일과 주주총회일 사이 기간', '개최장소', '주주총회 관련사항 주주통보 방법', '외국인 주주가 이해가능한 소집통지', '통지방법', '세부사항', '감사 또는 감사위원 출석 현황', '주주발언 주요 내용'],
    },
    '1-2-1': {
        "aclass": 'krx-cg_GeneralInformationRegardingAccessibilityToExerciseTheVotingRightsOfShareholdersAbstract',
        "title_ko": '정기 주주총회 의결권 행사 접근성',
        "title_en": 'Access to the Exercise of the Voting Right at AGM',
        "axis": 'row',
        "labels": ['구분', '정기주주총회 집중일', '정기주주총회일', '정기주주총회 집중일 회피 여부', '서면투표 실시 여부', '전자투표 실시 여부', '의결권 대리행사 권유 여부'],
    },
    '1-2-2': {
        "aclass": 'krx-cg_VotingResultsOfTheGeneralMeetingOfShareholdersAbstract',
        "title_ko": '주주총회 의결 내용',
        "title_en": "Resolutions from General Shareholders' Meetings",
        "axis": 'col',
        "labels": ['', '결의 구분', '회의 목적사항', '가결 여부', '의결권 있는 발행주식 총수(1)', '(1) 중 의결권 행사 주식수', '찬성주식수', '찬성 주식 비율 (%)', '반대 기권 등 주식수', '반대 기권 등 주식 비율 (%)'],
    },
    '1-3-1': {
        "aclass": 'krx-cg_ShareholderProposalsAbstract',
        "title_ko": '주주 제안 현황',
        "title_en": 'Status of Shareholder Proposals',
        "axis": 'col',
        "labels": ['', '제안 일자', '제안주체', '구분', '주요 내용', '처리 및 이행 상황', '가결 여부', '찬성률 (%)', '반대율 (%)'],
    },
    '1-3-2': {
        "aclass": 'krx-cg_OpenLettersAbstract',
        "title_ko": '공개서한 현황',
        "title_en": 'Status of Open Letters',
        "axis": 'col',
        "labels": ['', '발송일자', '주체', '주요 내용', '회신 일자', '수용 여부', '회신 주요 내용'],
    },
    '1-4-1': {
        "aclass": 'krx-cg_RecordDateAndTheFixedDateOfDividendAmountAbstract',
        "title_ko": '배당기준일과 배당액 확정일',
        "title_en": 'Dividend Record Date and Dividend Amount Confirmation Date',
        "axis": 'col',
        "labels": ['', '결산월', '결산배당 여부', '배당기준일', '배당액 확정일', '현금배당 관련 예측가능성 제공 여부'],
    },
    '1-5-1-1': {
        "aclass": 'krx-cg_DividendsForPast3FiscalYearsAbstract',
        "title_ko": '최근 3개 사업연도 주주환원 현황',
        "title_en": 'Shareholder Returns for the Last Three Business Years',
        "axis": 'col',
        "labels": ['', '일반현황', '주식배당', '현금배당(단위 : 원)'],
    },
    '1-5-1-2': {
        "aclass": 'krx-cg_PayoutRatioForPast3FiscalYearsAbstract',
        "title_ko": '최근 3개 사업연도 현금배당 성향',
        "title_en": 'Cash Dividend Payout Ratio for the Last Three Business Years',
        "axis": 'col',
        "labels": ['구분', '당기', '전기', '전전기'],
    },
    '2-1-1-1': {
        "aclass": 'krx-cg_NumberOfAuthorizedSharesAbstract',
        "title_ko": '발행가능 주식총수(주)',
        "title_en": 'Total Number of Authorized Shares (Unit: Shares)',
        "axis": 'col',
        "labels": ['보통주', '종류주', '발행가능 주식전체'],
    },
    '2-1-1-2': {
        "aclass": 'krx-cg_DetailsOfIssuedSharesAbstract',
        "title_ko": '주식발행 현황 세부내용',
        "title_en": 'Detailed Status of Stock Issuance',
        "axis": 'col',
        "labels": ['', '발행주식수(주)', '발행비율 (%)', '비고'],
    },
    '2-1-3': {
        "aclass": 'krx-cg_DetailsOfDesignationOfUnfaithfulDisclosureCorporationsAbstract',
        "title_ko": '불성실공시법인 지정 내역',
        "title_en": 'Details on Designation as Unfaithful Disclosure Corporation',
        "axis": 'col',
        "labels": ['', '불성실 공시유형', '지정일', '지정사유', '부과벌점', '제재금(단위 : 원)', '지정 후 개선노력 등'],
    },
    '4-1-2': {
        "aclass": 'krx-cg_CompositionOfTheBoardOfDirectorsAbstract',
        "title_ko": '이사회 구성 현황',
        "title_en": 'Composition of the Board',
        "axis": 'col',
        "labels": ['', '구분', '성별', '나이(滿)', '직책', '이사 총 재직기간(월)', '임기만료예정일', '전문 분야', '주요 경력'],
    },
    '4-1-3-1': {
        "aclass": 'krx-cg_SummaryOfCommitteeUnderTheBoardOfDirectorsAbstract',
        "title_ko": '이사회내 위원회 현황',
        "title_en": 'Status of the Committees of the Board',
        "axis": 'col',
        "labels": ['', '이사회내 위원회 주요 역할', '위원회 총원(명)', '위원회 코드', '비고'],
    },
    '4-1-3-2': {
        "aclass": 'krx-cg_CompositionOfCommitteeUnderTheBoardOfDirectorsAbstract',
        "title_ko": '이사회내 위원회 구성',
        "title_en": 'Composition of the Committees of the Board',
        "axis": 'col',
        "labels": ['', '직책', '구분', '성별', '겸임'],
    },
    '4-2-1': {
        "aclass": 'krx-cg_DirectorAppointmentAndStatusChangesOfTheDirectorsAbstract',
        "title_ko": '이사 선임 및 변동 내역',
        "title_en": 'Appointment and Changes of Directors',
        "axis": 'col',
        "labels": ['', '구분', '최초선임일', '임기만료(예정)일', '변동일', '변동사유', '현재 재직 여부'],
    },
    '4-3-1': {
        "aclass": 'krx-cg_InformationOnDirectorCandidatesAbstract',
        "title_ko": '이사 후보 관련 정보제공 내역',
        "title_en": 'Information Provided on Director Candidates',
        "axis": 'col',
        "labels": ['', '정보제공일(1)', '주주총회일(2)', '사전 정보제공기간(일)((2)-(1))', '이사 후보 구분', '정보제공 내역', '비고'],
    },
    '4-4-1': {
        "aclass": 'krx-cg_ListOfRegisteredExecutiveOfficersAbstract',
        "title_ko": '등기 임원현황',
        "title_en": 'Status of Registered Executives',
        "axis": 'col',
        "labels": ['', '성별', '직위', '상근 여부', '담당업무'],
    },
    '5-1-1': {
        "aclass": 'krx-cg_TenureOfServiceOfEachIndependentDirectorAbstract',
        "title_ko": '보고서 제출일 현재 사외이사 재직기간',
        "title_en": 'Outside Directors’ Term Served as of the Report Submission Date',
        "axis": 'col',
        "labels": ['', '당사 재직기간(월)', '계열회사 포함 시 재직기간(월)'],
    },
    '5-2-1': {
        "aclass": 'krx-cg_ConcurrentPositionForEachIndependentDirectorAbstract',
        "title_ko": '사외이사 겸직 현황',
        "title_en": 'Status of Outside Directors’ Concurrent Employment',
        "axis": 'col',
        "labels": ['', '감사위원 여부', '최초선임일', '임기만료예정일', '현직', '겸직 현황'],
    },
    '5-3-1': {
        "aclass": 'krx-cg_MeetingsHeldExclusivelyForIndependentDirectorsAbstract',
        "title_ko": '사외이사만으로 이루어진 회의 내역',
        "title_en": 'Details of Meetings Attended Only by Outside Directors',
        "axis": 'col',
        "labels": ['', '정기/임시', '개최일자', '출석 사외이사(명)', '전체 사외이사(명)', '회의 사항', '비고'],
    },
    '7-1-1': {
        "aclass": 'krx-cg_HistoryOfBoardMeetingHeldWithinTheDisclosurePeriodAbstract',
        "title_ko": '이사회 개최 내역',
        "title_en": 'Details of the Board Meetings Convened',
        "axis": 'col',
        "labels": ['', '개최횟수', '평균 안건통지-개최간 기간(일)', '이사 평균 출석률 (%)'],
    },
    '7-2-1': {
        "aclass": 'krx-cg_BoardAttendanceRateAndAgendaApprovalRateOfIndividualDirectorsForPast3YearsAbstract',
        "title_ko": '최근 3년간 이사 출석률 및 안건 찬성률',
        "title_en": 'Directors’ Attendance at the Board Meetings and Approval Rate of Agenda Items for the Last Three Years',
        "axis": 'col',
        "labels": ['', '구분', '이사회 재직기간', '출석률 (%)', '찬성률 (%)'],
    },
    '8-2-1': {
        "aclass": 'krx-cg_DetailsOfDirectorRecommendationCommitteeHeldAbstract',
        "title_ko": '이사후보추천위원회 개최 내역',
        "title_en": 'Director Recommending Committee Meetings Convened',
        "axis": 'col',
        "labels": ['', '개최일자', '출석 인원', '정원', '안건', '가결 여부', '이사회 보고 여부'],
    },
    '8-2-2': {
        "aclass": 'krx-cg_DetailsOfRiskManagementCommitteeHeldAbstract',
        "title_ko": '리스크관리위원회 개최 내역',
        "title_en": 'Risk Management Committee Meetings Convened',
        "axis": 'col',
        "labels": ['', '개최일자', '출석 인원', '정원', '안건', '가결 여부', '이사회 보고 여부'],
    },
    '8-2-3': {
        "aclass": 'krx-cg_DetailsOfInternalTradingCommitteeHeldAbstract',
        "title_ko": '내부거래위원회 개최 내역',
        "title_en": 'Internal Transaction Committee Meetings Convened',
        "axis": 'col',
        "labels": ['', '개최일자', '출석 인원', '정원', '안건', '가결 여부', '이사회 보고 여부'],
    },
    '9-1-1': {
        "aclass": 'krx-cg_CompositionOfTheInternalAuditTeamAbstract',
        "title_ko": '내부감사 기구의 구성',
        "title_en": 'Composition of the Internal Auditing Bodies',
        "axis": 'col',
        "labels": ['', '구성', '감사업무 관련 경력 및 자격', '비고'],
    },
    '9-2-1': {
        "aclass": 'krx-cg_AttendanceRateOfIndividualDirectorsInAuditCommitteeMeetingsInTheLast3YearsAbstract',
        "title_ko": '최근 3개년 개별이사 감사위원회 출석률',
        "title_en": 'Attendance Rate of Individual Directors to Audit Committee Meetings for the Last Three Years',
        "axis": 'col',
        "labels": ['', '구분', '출석률 (%)'],
    },
    '10-2-1': {
        "aclass": 'krx-cg_CommunicationDetailsWithExternalAuditorsAbstract',
        "title_ko": '외부감사인과 소통내역',
        "title_en": 'Details of Communication with External Auditor',
        "axis": 'col',
        "labels": ['', '개최일자', '분기', '진행 방식', '참석자', '회의 주요내용'],
    },
    '10-2-2': {
        "aclass": 'krx-cg_DetailsOfFinancialStatementsProvidedToExternalAuditorsAbstract',
        "title_ko": '재무제표 외부감사인 제공 내역',
        "title_en": 'Financial Statements Provided to the External Auditor',
        "axis": 'col',
        "labels": ['', '정기주총일', '재무제표 제공일자', '연결재무제표 제공일자', '제공대상'],
    },
    '11-1': {
        "aclass": 'krx-cg_DisclosureStatusOfTheCompanysCorporateValueupPlanAndParticipationOfTheBoardOfDirectorsAbstract',
        "title_ko": '기업가치 제고 계획 공시 현황 및 이사회 참여 여부',
        "title_en": 'Disclosure Status of Corporate Value-up Plans and Board’s Involvement',
        "axis": 'col',
        "labels": ['', '공시일자', '이사회 참여 여부', '관련 이사회 일자', '주요 논의 내용'],
    },
    '11-2': {
        "aclass": 'krx-cg_StatusOfTheCommunicationDoneBasedOnCorporateValueupPlansAbstract',
        "title_ko": '기업가치 제고 계획 소통 현황',
        "title_en": 'Status of the Communication Done Based on Corporate Value-up Plans',
        "axis": 'col',
        "labels": ['', '일자', '소통 대상', '소통 채널', '임원 참여 여부', '주요 소통 내용'],
    },
}


def section_path(code: str) -> tuple[str, str, str]:
    """섹션 코드를 (장, 핵심원칙, 세부원칙) 로 쪼갠다. 골격 코드는 빈 값을 돌려준다."""
    if not (len(code) == 6 and code.isdigit()) or code.startswith("0000"):
        return ("", "", "")
    chapter, principle, sub = code[0], code[1:3], code[3]
    return (
        chapter,
        str(int(principle)) if principle != "00" else "",
        f"{int(principle)}-{sub}" if principle != "00" and sub != "0" else "",
    )


def form_table(number: str) -> dict[str, Any] | None:
    """표 번호로 서식 명세를 찾는다. 회사가 덧붙인 표는 없으므로 None."""
    return FORM_TABLES.get(number)


#: 열 축 표의 선두 몇 칸은 머리글이 비어 있다(이사 이름·주총 회차 등이 놓이는 키 열).
#: 그 칸에 줄 이름을 여기 적어 둔 표만 추출한다 — 이름 없는 열을 순서로 부르지 않는다.
KEY_LABELS: dict[str, list[str]] = {
    "1-1-1": ["주주총회"],
    "1-2-1": ["주주총회"],
    "1-2-2": ["주주총회", "의안"],
    "4-2-1": ["이사"],
    "4-3-1": ["주주총회", "후보"],
    "5-2-1": ["사외이사"],
    "7-1-1": ["구분"],
    "7-2-1": ["이사"],
    "9-1-1": ["성명"],
    "10-2-1": ["회차"],
}

#: 첫 키 열이 주주총회를 가리켜야 하는 표. 서식이 이름을 안 달아 둔 칸이라 회사가 후보
#: 이름을 대신 적기도 한다 — 그 모양이 아니면 키 이름을 붙이지 않는다.
#: 「주주총회: 최춘웅」 이 나가는 것보다 이름 없는 열이 낫다.
_AGM_KEY_RE = re.compile(r"주총|주주총회|총회|정기|임시|\d+\s*기|\d{4}")
_KEY_SHAPES: dict[str, re.Pattern[str]] = {
    "1-1-1": _AGM_KEY_RE,
    "1-2-1": _AGM_KEY_RE,
    "1-2-2": _AGM_KEY_RE,
    "4-3-1": _AGM_KEY_RE,
}
_KEY_SHAPE_MIN = 0.7

_TABLE_NUM_RE = re.compile(r"^표\s*([\d\-]+)\s*[:：]")


def _text(node: Any) -> str:
    return re.sub(r"\s+", " ", " ".join(node.itertext())).strip()


def _span(cell: Any, attr: str) -> int:
    """서식은 병합 없는 칸에도 `colspan="0"` 을 달아 내보낸다 — 0 과 결측은 1칸이다."""
    try:
        return max(1, int(cell.get(attr) or 1))
    except ValueError:
        return 1


def _expand(
    rows: list[Any], carry_down: bool = True
) -> tuple[dict[tuple[int, int], str], int]:
    """rowspan·colspan 을 펼쳐 (행, 열) → 값 격자로 만든다.

    `carry_down=False` 는 rowspan 을 무시한다. 본문 행은 rowspan 이 걸려 있어도 아래 행이
    같은 값을 다시 싣기 때문에, 내려 채우면 그 행부터 열이 한 칸씩 밀린다.
    """
    grid: dict[tuple[int, int], str] = {}
    width = 0
    for r, tr in enumerate(rows):
        c = 0
        for cell in tr:
            while (r, c) in grid:
                c += 1
            cols = _span(cell, "colspan")
            span_rows = _span(cell, "rowspan") if carry_down else 1
            value = (cell.get("value") or "").strip() or _text(cell)
            for dr in range(span_rows):
                for dc in range(cols):
                    grid[(r + dr, c + dc)] = value
            c += cols
        width = max(width, c)
    return grid, width


def _column_labels(header_rows: list[Any], key_labels: list[str]) -> list[str]:
    """머리글 여러 층을 열마다 위에서 아래로 이어 붙인다. 빈 선두 칸은 키 이름으로 채운다."""
    grid, width = _expand(header_rows)
    labels: list[str] = []
    for c in range(width):
        parts: list[str] = []
        for r in range(len(header_rows)):
            value = grid.get((r, c), "")
            if value and (not parts or parts[-1] != value):
                parts.append(value)
        labels.append(" · ".join(parts))
    leading = 0
    while leading < len(labels) and not labels[leading]:
        leading += 1
    if leading != len(key_labels):
        return []
    return list(key_labels) + labels[leading:]


def _parse_fact_table(table: Any, number: str) -> dict[str, Any] | None:
    rows = table.findall(".//tr")
    header_rows = [tr for tr in rows if len(tr) and all(cell.tag == "th" for cell in tr)]
    body_rows = rows[len(header_rows):]
    if not header_rows or not body_rows:
        return None
    labels = _column_labels(header_rows, KEY_LABELS.get(number, []))
    if not labels:
        return None
    # 행마다 칸 수가 머리글과 맞는지 따로 본다 — 한 행이라도 어긋나면 그 아래가 통째로
    # 밀린 값을 이름에 붙이게 되므로, 맞추기보다 그 표를 내지 않는다.
    grid, width = _expand(body_rows, carry_down=False)
    if width != len(labels):
        return None
    records = []
    for r in range(len(body_rows)):
        if sum(_span(cell, "colspan") for cell in body_rows[r]) != width:
            return None
        record = {labels[c]: grid.get((r, c), "") for c in range(width)}
        if any(record.values()):
            records.append(record)
    keys = KEY_LABELS.get(number, [])
    if keys and not _key_column_holds(records, labels[0], _KEY_SHAPES.get(number)):
        labels = [f"키{i + 1}" for i in range(len(keys))] + labels[len(keys):]
        records = [dict(zip(labels, row.values())) for row in records]
        return {"columns": labels, "rows": records, "key_labels_verified": False}
    return {"columns": labels, "rows": records, "key_labels_verified": True}


def _parse_row_axis_table(table: Any, number: str) -> dict[str, Any] | None:
    """항목이 행에, 기수가 열에 놓인 표를 한 기수 = 한 줄로 뒤집는다.

    이 표들은 열 축 표와 달리 **rowspan 이 진짜 병합**이라(부모 라벨을 아래 행이 다시 싣지 않는다)
    라벨 격자에서는 rowspan 을 존중한다. 첫 행의 값이 기수 이름이고 나머지 행의 머리글이 항목이다.
    """
    rows = table.findall(".//tr")
    keys = KEY_LABELS.get(number, [])
    if len(rows) < 2 or len(keys) != 1:
        return None
    grid: dict[tuple[int, int], str] = {}
    label_width = 0
    values: list[list[str]] = []
    for r, tr in enumerate(rows):
        c = 0
        row_values: list[str] = []
        for cell in tr:
            if cell.tag == "th" and not row_values:
                while (r, c) in grid:
                    c += 1
                cols, span_rows = _span(cell, "colspan"), _span(cell, "rowspan")
                text = (cell.get("value") or "").strip() or _text(cell)
                for dr in range(span_rows):
                    for dc in range(cols):
                        grid[(r + dr, c + dc)] = text
                c += cols
                label_width = max(label_width, c)
            else:
                row_values.extend([(cell.get("value") or "").strip() or _text(cell)] * _span(cell, "colspan"))
        values.append(row_values)
    periods = values[0]
    if not periods or any(len(v) != len(periods) for v in values[1:]):
        return None
    labels: list[str] = []
    for r in range(len(rows)):
        parts: list[str] = []
        for c in range(label_width):
            text = grid.get((r, c), "")
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        labels.append(" · ".join(parts))
    columns = list(keys) + [labels[r] or f"항목{r}" for r in range(1, len(rows))]
    records = [
        {keys[0]: periods[i], **{labels[r] or f"항목{r}": values[r][i] for r in range(1, len(rows))}}
        for i in range(len(periods))
    ]
    if not _key_column_holds(records, columns[0], _KEY_SHAPES.get(number)):
        columns = ["키1"] + columns[1:]
        records = [dict(zip(columns, rec.values())) for rec in records]
        return {"columns": columns, "rows": records, "key_labels_verified": False}
    return {"columns": columns, "rows": records, "key_labels_verified": True}


def _key_column_holds(records: list[dict[str, str]], label: str, shape: re.Pattern[str] | None) -> bool:
    if shape is None:
        return True
    values = [str(row.get(label) or "").strip() for row in records]
    values = [v for v in values if v and v != "-"]
    if not values:
        return True
    return sum(1 for v in values if shape.search(v)) / len(values) >= _KEY_SHAPE_MIN


def parse_form_tables(html: str, numbers: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """서식 표를 표 번호별로 뽑는다.

    `numbers` 를 주면 그중 `KEY_LABELS` 에 이름이 있는 표만, 주지 않으면 이름이 있는 표
    전부를 대상으로 한다. 표가 원문에 없거나 서식과 어긋나면 그 번호는 결과에서 빠진다.
    """
    wanted = set(numbers or KEY_LABELS) & set(KEY_LABELS)
    if not html or not wanted:
        return {}
    tree = lxml_html.fromstring(html)
    found: dict[str, dict[str, Any]] = {}
    for group in tree.iter("table-group"):
        aclass = group.get("aclass") or ""
        if not aclass.startswith("krx-cg_"):
            continue
        node, label = group.getprevious(), None
        for _ in range(3):
            if node is None:
                break
            if node.tag == "p" and "table-name" in (node.get("class") or ""):
                label = _text(node)
                break
            node = node.getprevious()
        matched = _TABLE_NUM_RE.match(label or "")
        if not matched:
            continue
        number = matched.group(1)
        if number not in wanted or number in found:
            continue
        spec = FORM_TABLES.get(number)
        table = group.find('.//table[@class="fact-table"]')
        if spec is None or table is None or spec["aclass"] != aclass:
            continue
        parsed = (
            _parse_row_axis_table(table, number)
            if spec["axis"] == "row"
            else _parse_fact_table(table, number)
        )
        if parsed:
            found[number] = {"title": spec["title_ko"], **parsed}
    return found
