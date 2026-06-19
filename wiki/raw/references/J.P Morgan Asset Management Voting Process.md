
1. Admin: Proxy Administrator
2. Decision: Prescribed guideline vs. Case-by-Case Exception
3. Exception: Override, Escalation, Split Voting, Conflict Delegation
4. Control: Proxy Committee, Record-keeping


```mermaid
flowchart TD
    A[Proxy item received] --> B[Proxy Administrator oversees process]
    B --> C[Check whether JPMAM can / should vote]

    C --> C1{Voting possible?}
    C1 -- No --> X1[Do not vote / abstain
    Examples:
    material conflict
    securities on loan not recalled
    share blocking or regulatory restrictions
    late or insufficient proxy materials
    cost or burden outweighs benefit] 
    C1 -- Yes --> D[Classify item under JPMAM Guidelines]

    D --> D1{Prescribed Guideline?}
    D1 -- Yes --> E[Vote according to JPMAM Prescribed Guidelines]
    D1 -- No --> F[Case-by-case analysis]

    E --> E1{Need Override?}
    E1 -- No --> V[Cast vote]
    E1 -- Yes --> O1[Proxy Administrator reviews
    PM / analyst / stewardship input]
    O1 --> O2{Further review needed?}
    O2 -- Yes --> O3[Escalate to Proxy Committee]
    O2 -- No --> O4[Document override rationale
    + conflict/MNPI attestation]
    O3 --> O4
    O4 --> V

    F --> F1{Escalated vote needed?}
    F1 -- No --> F2[Proxy Administrator applies
    JPMAM history and experience]
    F1 -- Yes --> F3[Share research with
    portfolio management teams
    including stewardship / third-party research]
    F3 --> F4[Portfolio management team makes recommendation]
    F4 --> F5{Further escalation to Proxy Committee?}
    F5 -- Yes --> F6[Proxy Committee review]
    F5 -- No --> F7[Finalize recommendation]
    F6 --> F7
    F2 --> R1[Record significant decision
    + conflict/MNPI attestation]
    F7 --> R1
    R1 --> V

    V --> S1{Split voting needed?}
    S1 -- No --> G[Vote recorded]
    S1 -- Yes --> S2[Each portfolio team may vote differently
    for its client accounts]
    S2 --> S3[Team provides instructions
    rationale and attestation]
    S3 --> G

    G --> H{Conflict or special delegation case?}
    H -- Yes --> I[Delegate / abstain via
    Independent Voting Service
    where applicable]
    H -- No --> J[Maintain records]

    I --> J
    J --> K[Record-keeping:
    proxy statement
    vote cast
    decision documents
    issuer dialogue records
    client request/response]
```


## ASIA ex-Japan Proxy Voting Guidelines

