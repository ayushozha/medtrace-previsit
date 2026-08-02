# MedTrace clinical persistence design

Medplum FHIR R4 is the only clinical system of record. The repository-owned local stack is
defined in `infra/medplum/docker-compose.yml` and runs Medplum server/admin with internal
PostgreSQL and Redis. PostgreSQL and Binary data use named Docker volumes.

## Ownership

| Concept | Canonical FHIR R4 representation |
|---|---|
| Patient and stable AI-memory identity | `Patient`; Zep key in `Patient.identifier` |
| Primary clinician | `Practitioner` + `PractitionerRole`; `Patient.generalPractitioner` |
| Conditions | `Condition` |
| Medication history | `MedicationStatement` |
| Allergies | `AllergyIntolerance` |
| Labs and vital signs | `Observation` |
| Visits | `Encounter` |
| Uploaded source files | `Binary` + patient-linked `DocumentReference` |
| Chat thread and turns | Parent/child `Communication` resources using `partOf` |
| Extraction audit | `Provenance` |
| Extraction/projection workflow | `Task` |

Zep is a subordinate semantic memory and knowledge-graph projection. It never owns or updates
canonical clinical facts or transcripts. Imaging metadata/review state is canonical as
`ImagingStudy`, `DiagnosticReport`, and `Task`; voice visits are `Encounter` plus patient-scoped
transcript, report, and audio `DocumentReference`/`Binary` resources. Local files and SQLite are
derived viewer/cache artifacts only.

## Write sequences

Document upload:

1. Validate the patient and input.
2. Store bytes as `Binary`.
3. Create the patient-linked `DocumentReference`.
4. Create a processing `Task`.
5. Extract typed facts.
6. Conditionally write facts plus `Provenance` in a FHIR transaction.
7. Project text into Zep and complete the task, or leave a safe retry state.

Chat turn:

1. Conditionally create the user `Communication` using the client `request_id`.
2. Build context from canonical Communications and the FHIR clinical snapshot.
3. Create the assistant `Communication`.
4. Create a durable Zep-projection `Task`.

## Verification state

- AI-created `Condition` and `AllergyIntolerance` resources are `unconfirmed`.
- AI-created `Observation` resources are `preliminary`.
- Generated facts and source documents carry the `ai-extracted-unverified` tag.
- `Provenance` records the source document, extraction activity, timestamp, and model identity.
- Standard clinical codes are added only when a reliable mapping is available; otherwise
  `CodeableConcept.text` preserves the source wording.

## Local deployment

```bash
npm run medplum:up
# Create a project and ClientApplication at http://localhost:3002 and fill .env.
npm run medplum:bootstrap
npm run medplum:seed
```

The local stack is for synthetic evaluation and is not production/PHI-ready. Production operation
requires least-privilege AccessPolicy review, caller authentication, encryption, backups, audit
retention, monitoring, key rotation, upgrades, and disaster-recovery ownership.
