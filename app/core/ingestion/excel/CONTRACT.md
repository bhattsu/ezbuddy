# Excel → parser field contract (Texas_Divorce.xlsx reference)

| Sheet | Excel column | Parser field | Type | Required |
|-------|----------------|--------------|------|----------|
| Package | Package ID | `package_id` | str | Yes |
| Package | Package Name | `package_name` | str | Yes |
| Package | Practice Area | `practice_area` | str | Yes |
| Package | Jurisdiction | `jurisdiction` | str | Yes |
| Workflows | Workflow Code | `workflow_code` | str | Yes |
| Workflows | Workflow Name | `workflow_name` | str | Yes |
| Question Library | Question Code | `question_code` | str | Yes |
| Question Library | Question | `question` | str | Yes |
| Question Library | Input Type | `input_type` | str | Yes |
| Question Library | Data Type | `data_type` | str | Yes |
| Question Library | Lookup Code | `lookup_code` | str | If dropdown |
| Workflow Mapping | Workflow Code | `workflow_code` | str | Yes |
| Workflow Mapping | Question Code | `question_code` | str | Yes |
| Workflow Mapping | Display Order | `display_order` | int | Yes |
| Lookup Lists | Lookup Code | `lookup_code` | str | Yes |
| Lookup Lists | Value | `value` | str | Yes |
| Documents | Document Code | `document_code` | str | Yes |
| Documents | Workflow Code | `workflow_code` | str | Yes |
| Knowledge | Knowledge Code | `knowledge_code` | str | Yes |

Full header maps live in `constants.py` (`*_COLUMNS`).
