
from typing import Dict, Any, Optional
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

OUTPUT_FORMAT_PATH = Path(__file__).parent.parent / "config" / "output_format.json"


def convert_to_json_schema(output_format: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert output format dict to JSON schema for Bedrock structured output.

    Args:
        output_format: Dictionary representing the desired output structure

    Returns:
        JSON schema dict compatible with Bedrock's structured output
    """
    def infer_schema(value: Any) -> Dict[str, Any]:
        """Recursively infer JSON schema from Python dict/list structure"""
        if isinstance(value, dict):
            properties = {}
            required = []

            for k, v in value.items():
                if v is not None:
                    properties[k] = infer_schema(v)
                    # Mark non-optional fields as required (simple heuristic)
                    if not isinstance(v, (dict, list)) or (isinstance(v, dict) and v) or (isinstance(v, list) and v):
                        required.append(k)

            schema = {
                "type": "object",
                "properties": properties
            }
            if required:
                schema["required"] = required
            return schema

        elif isinstance(value, list):
            if len(value) > 0:
                # Infer schema from first item
                items_schema = infer_schema(value[0])
            else:
                # Empty list - use generic object schema
                items_schema = {"type": "object"}

            return {
                "type": "array",
                "items": items_schema
            }
        elif isinstance(value, bool):
            return {"type": "boolean"}
        elif isinstance(value, int):
            return {"type": "integer"}
        elif isinstance(value, float):
            return {"type": "number"}
        elif isinstance(value, str):
            return {"type": "string"}
        else:
            # Default to string for unknown types
            return {"type": "string"}

    # Convert the output format to JSON schema
    schema = infer_schema(output_format)

    return schema

def load_output_format() -> Dict[str, Any]:
    """Load default output format template"""
    try:
        if OUTPUT_FORMAT_PATH.exists():
            with open(OUTPUT_FORMAT_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load output format from {OUTPUT_FORMAT_PATH}: {str(e)}")
        pass

    # Fallback to default format if file doesn't exist or fails to load
    return {
        "document_type": "Invoice/Receipt/Form/Contract/Report/Medical Record/etc.",
        "extracted_fields": {
            "description": "All key-value pairs found in the document",
            "fields": {}
        },
        "tables": [
            {
                "table_name": "string",
                "headers": ["column1", "column2"],
                "rows": [
                    ["value1", "value2"]
                ]
            }
        ],
        "summary": "Brief description of document content",
        "metadata": {
            "total_fields_extracted": 0,
            "confidence": 0.0
        }
    }


def build_key_value_extraction_prompt(
    text: str,
    forms: list,
    tables: list,
    file_name: str = "",
    file_type: str = "",
    pages_processed: int = 0,
    total_pages: int = 0,
    custom_output_format: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Build prompt for extracting key-value pairs from document
    Supports custom output format

    Args:
        text: Extracted text content
        forms: List of form fields
        tables: List of tables
        file_name: Name of the file
        file_type: Type of the file
        pages_processed: Number of pages processed
        total_pages: Total number of pages
        custom_output_format: Optional custom JSON structure for output
    """


    output_format = custom_output_format if custom_output_format else load_output_format()
    from app.core.prompts.context import get_current_date_context

    date_ctx = get_current_date_context()
    format_instruction = ""
    if custom_output_format:
        format_instruction = """
CUSTOM OUTPUT FORMAT PROVIDED
The user has specified a custom output format. You MUST follow this exact structure.
Do not use the default format - use ONLY the format specified below.
"""

    instruction = f"""You are a document data extraction specialist. Your task is to extract ALL information from the document and format it according to the EXACT JSON structure provided below.

## Today's date
Today is {date_ctx["current_date_long"]} ({date_ctx["current_date"]}). Use this when interpreting document dates.

{format_instruction}

📄 DOCUMENT DETAILS
File: {file_name}
Type: {file_type}
Pages: {pages_processed}/{total_pages}

📝 FORM FIELDS ({len(forms)} fields)

{json.dumps(forms, indent=2) if forms else "No form fields detected"}

📊 TABLES ({len(tables)} tables)

{json.dumps(tables, indent=2) if tables else "No tables detected"}

📄 FULL TEXT CONTENT
{text[:4000]}
{"..." if len(text) > 4000 else ""}

🎯 YOUR TASK - EXTRACT DATA IN EXACT STRUCTURE

CRITICAL INSTRUCTIONS - FOLLOW THESE STRICTLY:

1. STRUCTURE COMPLIANCE (MANDATORY):
   - You MUST return a JSON object that matches the EXACT structure provided below
   - Use the field names EXACTLY as shown in the format (case-sensitive)
   - Maintain the exact nesting structure (objects, arrays, etc.)
   - Do NOT add any fields that are not in the format
   - Do NOT remove any fields from the format
   - Do NOT change field names or restructure the format

2. DATA EXTRACTION:
   - Extract ALL information from the document (text, forms, tables)
   - Map extracted data to the corresponding fields in the format
   - If information is found, populate the field with the extracted value
   - If a field is not found in the document, use these defaults:
     * Empty string "" for text/string fields
     * 0 or 0.0 for number fields
     * [] for array fields
     * {{}} for object fields
     * false for boolean fields

3. DATA FORMATTING:
   - For dates, use ISO format (YYYY-MM-DD) when possible
   - For numbers, use appropriate numeric types (integer or float)
   - For strings, preserve the exact text from the document
   - For arrays, ensure all items follow the array item structure

4. OUTPUT REQUIREMENTS:
   - Return ONLY valid JSON - no explanations, no markdown, no code blocks
   - The response must be a single, complete JSON object
   - Start with {{ and end with }}
   - Ensure all quotes are properly escaped
   - No trailing commas
   - Valid JSON syntax only

5. STRUCTURE VALIDATION:
   - Before returning, verify your output matches the required structure exactly
   - All top-level keys must match the format
   - All nested structures must match the format
   - Field types must match (string, number, boolean, array, object)

REQUIRED OUTPUT FORMAT (FOLLOW THIS EXACT STRUCTURE):
{json.dumps(output_format, indent=2)}

EXAMPLE EXTRACTION PROCESS:
1. Read all text, forms, and tables from the document above
2. Identify which fields in the format can be populated from the document
3. Map each piece of extracted information to the corresponding field in the format
4. Fill in ALL fields in the format (use defaults for missing data)
5. Verify the output structure matches the format exactly
6. Return the complete JSON object

{"⚠️ CRITICAL: You are using a CUSTOM format. You MUST follow this structure EXACTLY. Do not add, remove, or modify any fields." if custom_output_format else "⚠️ Follow the structure above EXACTLY. Do not deviate from the field names or structure."}

FINAL REMINDER:
- Return ONLY the JSON object
- No markdown formatting (no ```json```)
- No explanatory text before or after
- The JSON must be valid and parseable
- The structure must match the format EXACTLY
"""

    prompt_body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": instruction}
            ]}
        ],
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 0.1
    }

    # Note: Structured output (response_format) requires bedrock-2024-06-20 or later API version
    # For bedrock-2023-05-31, we use prompt-based instructions instead
    # The format is already included in the instruction text above

    return prompt_body

def build_claude_prompt(text: str, structured_data: Dict[str, Any], base64_image: str = None) -> Dict[str, Any]:
    instruction = """
You are an AI that extracts schema definitions from document content for rendering with React JSON Schema Form (RJSF).

        Given:
        - Page text (for context and assistance)
        - Document image (source of truth for form layout and content)

        Your task is to output four valid schemas: jsonSchema, uiSchema, formSchema, and widgetSchema.


        I NEED STRICTLY VALID JSON OUTPUT in this exact format:
        {
          "jsonSchema": { },
          "uiSchema": { },
          "formSchema": [ ],
          "corrections": { }
        }

        ----------------------------
        STRICT INSTRUCTIONS
        ----------------------------

        1. IMAGE IS THE SOURCE OF TRUTH
        - Use the visual layout of the image as the primary reference, not the OCR.
        - Follow the exact order from top to bottom, left to right.
        - Group visually related fields as type "object".
        - Even if repeated fields are present do not omit any field.
        CRITICAL: Render the main form title exactly as shown in the document in the jsonSchema "title" property.
        When rendering content using `HtmlWidget`, always apply appropriate **Bootstrap 4 classes** for styling:
       -If the header contains image or logo
        - For section titles or emphasized headers, use: <div class=\\\"text-center font-weight-bold mb-2\\\">...</div> instead of any heading tag (<h1>–<h6>)
        - Like wise for button give its appropriate bootstrap class.


        2. FIELD NAMES (CRITICAL)
        - Use the full label exactly as shown in the form.
        - DO NOT modify, abbreviate, truncate, or paraphrase any field name or description.
        - Use Title Case for all field titles (capitalize major words).
        - Retain full medical or legal phrases as presented in the document.
        - IMPORTANT: Escape all quotes in text content (" → \\") - double backslash for proper escaping.
        - NEVER rename or restructure fields - keep exactly as in the original form.
        - Never subcategorize or group fields unless explicitly shown in the image.
        - Strictly follow this if a duplicate title is present, display it only once to avoid rendering it twice in the UI.
        Below json contains the example for the duplicate title where the title is "Mother/Stepmother/Guardian Information" and it is present twice in the json schema.
        motherInfo": {
        "type": "object",
        "title": "Mother/Stepmother/Guardian Information",
        "properties": {
          "name": {
            "type": "string",
            "title": "MOTHER/ STEPMOTHER/ GUARDIAN'S NAME"
          },
          "occupation": {
            "type": "string",
            "title": "Occupation/ Employer"
          },
          "address": {
            "type": "string",
            "title": "Address"
          },
          "phone": {
            "type": "string",
            "title": "Phone/ Mobile"
          },
          "email": {
            "type": "string",
            "title": "E-mail"
          }
        }
      },
      "fatherInfo": {
        "type": "object",
        "title": "Father/Stepfather/Guardian Information",
        "properties": {
          "name": {
            "type": "string",
            "title": "FATHER/ STEPFATHER/ GUARDIAN'S NAME"
          },
          "occupation": {
            "type": "string",
            "title": "Occupation/ Employer"
          },
          "address": {
            "type": "string",
            "title": "Address"
          },
          "phone": {
            "type": "string",
            "title": "Phone/ Mobile"
          },
          "email": {
            "type": "string",
            "title": "E-mail"
          }
        }
      },

        3. PARAGRAPH AND INSTRUCTIONAL TEXT

        - Any full sentences, instructions, disclaimers, or contextual notes MUST be rendered using:

        "ui:widget": "HtmlWidget",
        "ui:options": {
            "html": "<escaped full paragraph text>"
        }

        - Do NOT use "ui:description" or plain strings — these do not render HTML and will display tags as raw text.

        - ALWAYS escape double quotes in HTML content using "\\" and remove line breaks.

        - Instructional text must not be converted into form fields.

        - If the paragraph is visually present but not associated with a form field:
        - Create a virtual field named "instructions" in the schema under the correct section.
        - Example jsonSchema addition:
            ```json
           "instructions": {
            "type": "string",
            "title": "Instructions",
            "default": "<p>This is <strong>rich</strong> text content.</p>"
            }
            ```
        - Then define the UI schema for that field as:
            ```json
                "instructions": {
                "ui:widget": "HtmlWidget"
}
            ```

        - This ensures the paragraph is rendered even if the original form design does not associate it with a data-bound field.

        -  Never omit visible instructional or paragraph text from the form, even if it does not require user input. Preserve the visual order from top to bottom as seen in the source image.

        - Example:
        ```json
        "instructions": {
        "type": "string",
        "title": "Instructions",
        "default": "<p>Please arrive 15 minutes early for your appointment.</p><p>Bring a list of medications and prior reports if available.</p>"
        }


        example ui shcema:
        "instructions": {
        "ui:widget": "HtmlWidget"
        }


        4. TABLES
        - When generating schemas for tables in forms, ensure proper nesting of UI schema components to prevent duplicate keys and ensure correct rendering.
        - Use tables only when necessary.
        - For each array of objects in the schema (e.g. siblings, developmentalConcerns, etc.), include a ui:widget set to "TableHtmlWidget" and define ui:options.inputs as a two-dimensional array with each column represented as an object that includes name (matching the key in the schema) and label (the column title).
        - even if the data is cut off to the next page, the table should be rendered in the same way as it is in the original form.
        Example format for ui:options:

        {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "name", "label": "Sibling Name(s)" },
                { "name": "relation", "label": "Brother/Sister" },
                { "name": "age", "label": "Age" }
            ]
            ]
        }
        }
        When using the TableHtmlWidget component, ensure you provide the correct structure for the options.inputs property. The widget expects:

        1. The "inputs" property must be a two-dimensional array where the first element contains column definitions.
        2. Each column definition must have "name" and "label" properties.
        3. The "name" must match a property name in your schema.

        Example correct format:
        "fieldName": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "type", "label": "Type" },
                { "name": "dateOfBirth", "label": "Date of Birth" },
                { "name": "occupation", "label": "Occupation" }
            ]
            ]
        }
        }

        IMPORTANT: The "inputs" property MUST be a nested array with exactly one array inside containing column definitions. Don't flatten this structure or the widget will display "No table layout inputs defined."

        For the schema structure, ensure all field properties referenced in the table are properly defined:
        "fieldName": {
        "type": "array",
        "title": "Field Title",
        "items": {
            "type": "object",
            "properties": {
            "type": { "type": "string", "title": "Type" },
            "dateOfBirth": { "type": "string", "title": "Date of Birth" },
            "occupation": { "type": "string", "title": "Occupation" }
            }
        }
        }

        PREDEFINED ROW TABLES (FIXED IDENTITY FIELDS)
            Use this when the table contains specific labeled rows that cannot be added/removed (e.g., "Mother", "Father", "Siblings").
            If any predefined rows are present, the table should be rendered in the same way as it is in the original form .place it under default.

            Example for JSON Schema:

            developmentalConcerns": {
        "type": "array",
        "title": "Please indicate if you have had in the past or currently have any concerns in the following areas of development:",
        "items": {
          "type": "object",
          "properties": {
            "areaOfDevelopment": {
              "type": "string",
              "title": "Area of Development"
            },
            "pastConcern": {
              "type": "boolean",
              "title": "Past Concern"
            },
            "currentConcern": {
              "type": "boolean",
              "title": "Current Concern"
            }
          }
        },
        "default": [
          {
            "areaOfDevelopment": "Motor (e.g., crawling, sitting, walking, running, clumsiness)"
          },
          {
            "areaOfDevelopment": "Self-help (e.g., dressing, toileting)"
          },
          {
            "areaOfDevelopment": "Feeding (e.g., drooling, choking, sensitivity to textures)"
          },
          {
            "areaOfDevelopment": "Early play (e.g., using toys appropriately)"
          }
        ]
      }
            Example for JSON Schema:
            JSON Schema:
            json"familyTable": {
            "type": "object",
            "title": "Family Medical/Psychiatric",
            "properties": {
                "mother": {
                "type": "object",
                "title": "Mother",
                "properties": {
                    "education": { "type": "string", "title": "Highest Educational Level" },
                    "health": { "type": "string", "title": "Health Problems" },
                    "psychiatric": { "type": "string", "title": "Psychiatric" }
                }
                },
                "father": {
                "type": "object",
                "title": "Father",
                "properties": {
                    "education": { "type": "string", "title": "Highest Educational Level" },
                    "health": { "type": "string", "title": "Health Problems" },
                    "psychiatric": { "type": "string", "title": "Psychiatric" }
                }
                },
                "siblings": {
                "type": "array",
                "title": "Siblings",
                "items": {
                    "type": "object",
                    "properties": {
                    "education": { "type": "string", "title": "Highest Educational Level" },
                    "health": { "type": "string", "title": "Health Problems" },
                    "psychiatric": { "type": "string", "title": "Psychiatric" }
                    }
                }
                }
            }
            }
            UI Schema:
            json"familyTable": {
            "ui:widget": "PredefinedTableWidget",
            "ui:options": {
                "layout": "table",
                "identityColumn": "relation",
                "predefinedRows": [
                { "id": "mother", "label": "Mother" },
                { "id": "father", "label": "Father" },
                { "id": "siblings", "label": "Siblings", "isArray": true, "minItems": 3 }
                ],
                "columns": [
                { "name": "relation", "label": "Family Medical/Psychiatric", "readOnly": true },
                { "name": "education", "label": "Highest Educational Level" },
                { "name": "health", "label": "Health Problems" },
                { "name": "psychiatric", "label": "Psychiatric" }
                ]
            }
            }
        - Never use "TableWidget" or any unrecognized widget name.
        - The only supported table widget is: "ui:widget": "TableHtmlWidget"
        - automatically derive the title for tables based on the actual field text. For example, for the question "Do any other family members have speech, language, or related difficulties or disorders?", the title for the related table should be derived from this question, rather than being explicitly specified.
        - STRICTLY maintain the name of the field or title of the table as it is in the original form.

        You may render tables in the following valid ways:

        ---

         A. STATIC TABLES (READ-ONLY)

        Use this when the table is purely informational and not intended for user input.
        example for json schema for table:
        "html_field": {
        "type": "string",
        "title": " ",
        "default": "<table><tr><td>Better pattern</td></tr></table>"
        }


        example for ui schema for table:
        "html_field": {
        "ui:widget": "TableHtmlWidget"
        }

        Notes:
        - Escape all quotes in HTML (" → \\")
        - Remove all line breaks for JSON validity
        - Do not define any `field_key` or inputs for static tables

        ---

        B. FILLABLE TABLES (FIXED ROWS / SINGLE SET)

        Use this when users must fill out a known, fixed set of rows (e.g., family members, education history, health conditions).
        -If any predefined rows are present, the table should be rendered in the same way as it is in the original form.



        JSON Schema

        "html_table": {
        "type": "string",
        "title": " ",
        "default": "<table><thead><tr><th>Name</th><th>Age</th></tr></thead><tbody><tr><td>John</td><td>30</td></tr></tbody></table>"
        }

        UI Schema

        "html_table": {
        "ui:widget": "HtmlWidget"
        }





        JSON Schema

        "medicationTable": {
        "type": "array",
        "title": "Medications",
        "items": {
            "type": "object",
            "properties": {
            "name": { "type": "string", "title": "Medication Name" },
            "dose": { "type": "string", "title": "Dosage" },
            "frequency": { "type": "string", "title": "Frequency" }
            }
        }
        }

        UI Schema

        "medicationTable": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "name", "label": "Medication Name" },
                { "name": "dose", "label": "Dosage" },
                { "name": "frequency", "label": "Frequency" }
            ]
            ]
        }
        }

      json schema example:
        "mother_education": {
        "type": "string",
        "title": "Mother - Highest Educational Level"
        },
        "mother_health": {
        "type": "string",
        "title": "Mother - Health Problems"
        },
        "father_education": {
        "type": "string",
        "title": "Father - Highest Educational Level"
        },
        "father_health": {
        "type": "string",
        "title": "Father - Health Problems"
        }


        ui schema example:
        "family_info_group": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "mother_education", "label": "Mother - Highest Educational Level" },
                { "name": "mother_health", "label": "Mother - Health Problems" }
            ],
            [
                { "name": "father_education", "label": "Father - Highest Educational Level" },
                { "name": "father_health", "label": "Father - Health Problems" }
            ]
            ]
        }
        }

        CRITICAL:
        - Every `name` used in the `inputs` array MUST be defined in `jsonSchema` under the same object with:
        - a matching `"title"`
        - a valid `"type"` (typically `"string"`)

        Example in jsonSchema:

                json schema example:
        "mother_education": {
        "type": "string",
        "title": "Mother - Highest Educational Level"
        },
        "mother_health": {
        "type": "string",
        "title": "Mother - Health Problems"
        },
        "father_education": {
        "type": "string",
        "title": "Father - Highest Educational Level"
        },
        "father_health": {
        "type": "string",
        "title": "Father - Health Problems"
        }


        ui schema example:
        "family_info_group": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "mother_education", "label": "Mother - Highest Educational Level" },
                { "name": "mother_health", "label": "Mother - Health Problems" }
            ],
            [
                { "name": "father_education", "label": "Father - Highest Educational Level" },
                { "name": "father_health", "label": "Father - Health Problems" }
            ]
            ]
        }
        }


        Do not mix static HTML rows and dynamic inputs in the same table block.

        ---

        C. REPEATABLE TABLES (DYNAMIC ROWS)

        Use this when the user must be able to add or remove rows dynamically (e.g., list of medications, travel history, multiple children).

        json schema:
        "medicationTable": {
        "type": "array",
        "title": "Medications",
        "items": {
            "type": "object",
            "properties": {
            "name": { "type": "string", "title": "Medication Name" },
            "dose": { "type": "string", "title": "Dosage" },
            "frequency": { "type": "string", "title": "Frequency" }
            }
        }
        }


        ui schema:
        "medicationTable": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "name", "label": "Medication Name" },
                { "name": "dose", "label": "Dosage" },
                { "name": "frequency", "label": "Frequency" }
            ]
            ]
        }
        }

        This allows the user to dynamically add or remove rows, with consistent column structure.

        ---

        DO NOT:
        - Use `"TableWidget"` or any unsupported widget name.
        - Combine static and fillable content in the same table.
        - Leave `inputs` undefined or use fields not declared in `jsonSchema`.

        Ensure all repeatable or structured table cells use `type: object` (not just string arrays) and match schema definitions exactly.

        5. FIELD TYPES AND WIDGET MAPPINGS


        - A properly structured "uiSchema" object that correctly references the TableHtmlWidget, including any necessary "options" property with an "inputs" array that defines the table columns
        - If there are any links present in the form, include them appropriately.Do not ignore those.
         -Do not include same content in both title and default.
         - Do not convert the address and company information present in the header into editable fields. These should be treated as static, read-only content and must not appear as input fields in the generated schema
        - A matching "schema" object that defines the data structure, particularly focusing on array type fields with object items that have specific properties
        - Ensure the "inputs" option in the uiSchema is properly structured as an array of column definitions, where each column definition includes at least "name" and "label" properties
        - Include an example of initial form data that matches the schema structure
        - Proper nesting of all properties and objects
        - Even if its office or admin use do not ignore that fields too.
        - Include office use fields in json schema.
        - Correct placement of all array item definitions (including enums within items objects, not at the array level)
        - Appropriate type definitions for all properties (string, boolean, array, object, etc.)
        - Short text input → type: "string", ui:widget: "text"
        - Long text area → type: "string", ui:widget: "textarea"
        - Numbers → type: "integer", ui:widget: "updown"
        - Do not add a title to any property or object if it was not explicitly present in the original uploaded schema. If a title is missing, leave it missing. Do not infer or auto-generate a title from the property name or content.
        - For every object , properties or string in the schema that is missing a title or has an empty title (""), set the value of title to a single space " ".
        - Enumerated options → use "enum" with ui:widget: "select", "radio", or "checkboxes"
        - Repeated sections → type: "array", items: { type: "object", ... }
        - Strictly donot omit any field that is present in the original form.
         -DO not use default for multiple choice fields use enum with ui:widget: "checkboxes" or "radio" or "select
        -Avoid using "default" for checkbox or multi-select fields like medicalIssues, as it pre-selects all values when the form loads.
         Instead, let users actively select the options that apply to them to avoid incorrect assumptions or data entry errors.

        REQUIRED FIELDS:
        -If any field label contains an asterisk *, treat it as required:

        -Add the field to the "required" array of its parent object.

        -Add a corresponding custom error message under "errorMessage" → "required" using the field name.

        Strip the * from the label/title in the final schema.

        Strictly follow the below json for required fields by adding error message for required fields:

        {
          "type": "object",
          "properties": {
            "classroomType": {
              "type": "string",
              "title": "Type of Classroom"
            },
            "classroomTypeNote": {
              "type": "string",
              "title": " ",
              "default": "* Montessori, General Education, Special Education, etc."
            }
          },
          "required": ["classroomType"],
          "errorMessage": {
            "required": {
              "classroomType": "Please select the type of classroom."
            }
          }
        }
        {
          "type": "object",
          "properties": {
            "classroomType": {
              "type": "string",
              "title": "Type of Classroom"
            },
            "classroomTypeNote": {
              "type": "string",
              "title": " ",
              "default": "* Montessori, General Education, Special Education, etc."
            }
          },
          "required": ["classroomType"],
          "errorMessage": {
            "required": {
              "classroomType": "Please select the type of classroom."
            }
          }
        }


        Strictly follow the below for Phone number and date fields:
          -Use "format": "tel" for phone number fields (renders <input type="tel" />).
          -Use "format": "date" for date fields (renders a date picker with <input type="date" />).
          -Include minimal schema using type: "string" and the relevant format
        - Related fields → group inside type: "object"
        IMPORTANT: For array fields, NEVER use "ui:widget": "textarea" directly.
        Use:
        "uiSchema": {
        "fieldName": {
            "items": {
            "ui:widget": "textarea"
            }
        }
        }
        -For every field that uses "ui:widget": "checkboxes", please ensure that "uniqueItems": true is included to ensure it renders correctly.

        -Strictly do not create ui widget:checkbox for type boolean


        7. ERROR CORRECTION
        - Silently fix any typos, missing punctuation, or layout mismatches found in the image.
        - Record all changes in the "corrections" section of the output JSON.

        8. PRESERVATION OF MEDICAL CONTEXT
        - Do not omit, reword, or shorten any medical term or sentence.
        - Treat all text as clinically sensitive — accuracy is mandatory.

        9. FALLBACK INSTRUCTIONS
        - If a section cannot be translated using RJSF widgets, convert it into:
          type: "string", ui:widget: "textarea"

        10. OBJECT FIELD TITLES
        - For every field of type "object" without a title, add a title using its property key converted to Title Case.
        - Example: birthDifficulties → "title": "Birth Difficulties"

        11. JSON VALIDATION CRITICAL
        - Before returning your response, validate that it is proper JSON:
          - Ensure all quotes are properly closed and escaped
          - All brackets and braces must have matching pairs
          - No trailing commas in arrays or objects
          - No single quotes for properties or values, use double quotes only
          - String values must use double quotes and escape internal quotes
          - Verify the response is a single valid JSON object with the 5 required properties
          - All the html content must be rendered only in jsonSchema and uischema should define how to render the html content in the form

        12.ARRAY FIELD WIDGETS (CRITICAL - TO PREVENT ERRORS)

        NEVER apply "ui": "textarea" directly to an array field
        For array fields, widget settings must be applied to the items, not to the array itself:

        INCORRECT (will cause errors):
        json"uiSchema": {
        "myArrayField": {
            "ui:widget": "textarea"  // THIS WILL CAUSE THE ERROR
        }
        }
        CORRECT:
        json"uiSchema": {
        "myArrayField": {
            "items": {
            "ui:widget": "textarea"  // Apply to items, not to the array
            }
        }
        }

        For array fields with object items, apply widgets to the specific properties:

        json"uiSchema": {
        "myArrayField": {
            "items": {
            "someProperty": {
                "ui:widget": "textarea"
            }
            }
        }
        }

        sample output for JSON schema:
        {
        "title": "Employee Information",
        "type": "object",
        "properties": {
            "employees": {
            "type": "array",
            "title": "Employees",
            "items": {
                "type": "object",
                "properties": {
                "name": {
                    "type": "string",
                    "title": "Name"
                },
                "role": {
                    "type": "string",
                    "title": "Role"
                },
                "age": {
                    "type": "number",
                    "title": "Age"
                }
                },
                "required": ["name", "role", "age"]
            }
            }
        }
        }
        sample output for UISchema:
        {
        "employees": {
            "ui:widget": "table"
        }
        }
        sample HTML field for json schema:
        "html_field": {
        "title": " ",

        "type": "string"
        }
        sample HTML for UISchema:
        "html_field": {
        "ui:widget": "HtmlWidget"
        }
        13.YES/NO FIELDS (CRITICAL)
            For yes/no questions, ALWAYS use radio buttons with two options, not a single checkbox:
            json"jsonSchema": {
            "hasAllergies": {
                "type": "string",
                "title": "Do you have any allergies?",
                "enum": ["Yes", "No"]
            }
            }
            json"uiSchema": {
            "hasAllergies": {
                "ui:widget": "radio",
                "ui:options": {
                "inline": true
                }
            }
            }

            If the original form shows "Yes/No" options or check boxes for affirmative/negative responses, always implement as radio buttons with "Yes" and "No" values.
            If multiple yes/no options appear in sequence, still implement each as its own radio field.
        14.FORM TITLE :
            - Do not have a separate title field in the json schema.
            - Use the respective title inside the properties of the json schema.
            - If the title need to be included take it from the header of the document like company name.
            - Ensure to keep the table titles,headings,subheadings only inside properties of the json schema.
            - For every object ,string or properties in the schema that is missing a title or has an empty title (""), set the value of title to a single space " ".
            - If there is default value in the schema and no title is present, then set the title to a single space " ".
            Example for title empty or missing:
          "classroomType": {
            "type": "string",
            "title": "Type of Classroom*"
          },
          "classroomTypeNote": {
            "type": "string",
            "title":" ",
            "default": "* Montessori, General Education, Special Education, etc."
          }


        15. DOCUMENT STRUCTURE HANDLING
        - IGNORE page numbers, footers, headers, and document reference numbers.
        - DO NOT include page footer text like "Page X of Y" or addresses at the bottom of pages.
        - EXCLUDE any watermarks, form IDs, or revision dates that appear in headers/footers.
        - When content spans multiple pages, maintain logical structure regardless of page breaks.

        16.ACKNOWLEDGEMENTS AND POLICY TEXT
        - PRESERVE acknowledgement text EXACTLY as written - no summarizing or rewording.
          DECLARATION:
            - Do not miss any declaration if present in the document that are crucial to include in the json schema
            - Please include the content that appears after the input field in the Declaration section. Also, ensure the Declaration text that accompanies the Signature field is included as well.

        Do not summarize, omit, or rephrase any part of the declaration. Render the full declaration content exactly as provided, including blank lines for signatures or names, such as:
        sample declarations:
            ‘I, ____________________________________, have read and understand these policies. I agree to fully comply with what is written above. I give Milestones Educational Therapy Institute permission to charge my credit card on the first of every month.’”**
         Sample JSON :
                {
        "jsonSchema": {
            "type": "object",
            "properties": {
            "header_1": {
                "title":" ",
                "type": "string",
                "default": "<span>I,</span>"
            },
            "acknowledgmentName": {
                "type": "string",
                "title": " "
            },
            "footer_1": {
                "title":" ",
                "type": "string",
                "default": "<span>have read and understand these policies. I agree to fully comply with what is written above. I give Milestones Educational Therapy Institute permission to charge my credit card on the first of every month.</span>"
            }
            },
            "required": ["acknowledgmentName"]
        },
        "uiSchema": {
            "header_1": {
            "ui:widget": "HtmlWidget"
            },
            "acknowledgmentName": {
            "ui:options": {
                "label": false
            },
            "ui:placeholder": "Enter your full name"
            },
            "footer_1": {
            "ui:widget": "HtmlWidget"
            }
        }
        }

        Sample declaration:

            "I understand that Milestones Educational Therapy Institute does not contact, follow up with, or submit  paper work to insurance companies for reimbursement: "

        - Use the HtmlWidget for policy text and acknowledgements:
            ```json
            "acknowledgement": {
            "type": "string",
            "title": "Acknowledgement",
            "default": "<p>EXACT policy text here...</p>"
            }
            ```
        - In uiSchema:
            ```json
            "acknowledgement": {
            "ui:widget": "HtmlWidget"
            }
                ```


        18. OUTPUT FORMAT
        - Provide only the valid JSON object - no explanations, no markdown.
        - Output must start with '{' and end with '}' - a single complete JSON object.
        - Include all five required sections: jsonSchema, uiSchema, formSchema, widgetSchema, and corrections.
        - The formSchema should be an array, even if empty: []
        - If no corrections, use empty object: "corrections": {}

```
        """

    return {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "text", "text": f"Page Text:\n{text}"},
                {"type": "text",
                    "text": f"Structured Data (from Textract):\n{json.dumps(structured_data)}"},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64_image
                }}
            ]}
        ],
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8000,
        "temperature": 0.4
    }


def build_claude_prompt_image(text: str, structured_data: Dict[str, Any], base64_image: str = None) -> Dict[str, Any]:
    instruction = """
 You are an AI that extracts schema definitions from document content for rendering with React JSON Schema Form (RJSF).

        Given:
        - Page text (for context and assistance)
        - Document image (source of truth for form layout and content)

        Your task is to output four valid schemas: jsonSchema, uiSchema, formSchema, and widgetSchema.


        I NEED STRICTLY VALID JSON OUTPUT in this exact format:
        {
          "jsonSchema": { },
          "uiSchema": { },
          "formSchema": [ ],
          "corrections": { }
        }

        ----------------------------
        STRICT INSTRUCTIONS
        ----------------------------

        1. IMAGE IS THE SOURCE OF TRUTH
        - Use the visual layout of the image as the primary reference, not the OCR.
        - Follow the exact order from top to bottom, left to right.
        - Group visually related fields as type "object".
        - Even if repeated fields are present do not omit any field.
        CRITICAL: Render the main form title exactly as shown in the document in the jsonSchema "title" property.
        When rendering content using `HtmlWidget`, always apply appropriate **Bootstrap 4 classes** for styling:

        - For tables, always use: <table class=\\\"table table-bordered\\\">...</table>
        - For section titles or emphasized headers, use: <div class=\\\"text-center font-weight-bold mb-2\\\">...</div> instead of any heading tag (<h1>–<h6>)
        - Like wise for button give its appropriate bootstrap class.


        2. FIELD NAMES (CRITICAL)
        - Use the full label exactly as shown in the form.

        -If no title is present in the properties keep the title as empty string.
         Example:
           "privacyNotice_6": {
        "type": "object",
        "title": "NOTICE OF INFORMATION PRACTICES & PRIVACY",
        "properties": {
          "privacyText": {
            "title":"",
            "type": "string",
            "default": "<p>We keep a record of the health care services we provide you. You may ask to see and copy that record. We will not disclose your record to others unless you direct us to do so or unless the law authorizes or compels us to do so. To request a copy of your record or additional information about our privacy practices, please call XXXX.</p>"
          }
        }
      }
        - DO NOT modify, abbreviate, truncate, or paraphrase any field name or description.
        - Use Title Case for all field titles (capitalize major words).
        - Retain full medical or legal phrases as presented in the document.
        - IMPORTANT: Escape all quotes in text content (" → \\") - double backslash for proper escaping.
        - NEVER rename or restructure fields - keep exactly as in the original form.
        - Never subcategorize or group fields unless explicitly shown in the image.
        - Strictly follow this if a duplicate title is present, display it only once to avoid rendering it twice in the UI.
        Below json contains the example for the duplicate title where the title is "Mother/Stepmother/Guardian Information" and it is present twice in the json schema.
        motherInfo": {
        "type": "object",
        "title": "Mother/Stepmother/Guardian Information",
        "properties": {
          "name": {
            "type": "string",
            "title": "MOTHER/ STEPMOTHER/ GUARDIAN'S NAME"
          },
          "occupation": {
            "type": "string",
            "title": "Occupation/ Employer"
          },
          "address": {
            "type": "string",
            "title": "Address"
          },
          "phone": {
            "type": "string",
            "title": "Phone/ Mobile"
          },
          "email": {
            "type": "string",
            "title": "E-mail"
          }
        }
      },
      "fatherInfo": {
        "type": "object",
        "title": "Father/Stepfather/Guardian Information",
        "properties": {
          "name": {
            "type": "string",
            "title": "FATHER/ STEPFATHER/ GUARDIAN'S NAME"
          },
          "occupation": {
            "type": "string",
            "title": "Occupation/ Employer"
          },
          "address": {
            "type": "string",
            "title": "Address"
          },
          "phone": {
            "type": "string",
            "title": "Phone/ Mobile"
          },
          "email": {
            "type": "string",
            "title": "E-mail"
          }
        }
      },

        3. PARAGRAPH AND INSTRUCTIONAL TEXT

        - Any full sentences, instructions, disclaimers, or contextual notes MUST be rendered using:

        "ui:widget": "HtmlWidget",
        "ui:options": {
            "html": "<escaped full paragraph text>"
        }

        - Do NOT use "ui:description" or plain strings — these do not render HTML and will display tags as raw text.

        - ALWAYS escape double quotes in HTML content using "\\" and remove line breaks.

        - Instructional text must not be converted into form fields.

        - If the paragraph is visually present but not associated with a form field:
        - Create a virtual field named "instructions" in the schema under the correct section.
        - Example jsonSchema addition:
            ```json
           "instructions": {
            "type": "string",
            "title": "Instructions",
            "default": "<p>This is <strong>rich</strong> text content.</p>"
            }
            ```
        - Then define the UI schema for that field as:
            ```json
                "instructions": {
                "ui:widget": "HtmlWidget"
}
            ```

        - This ensures the paragraph is rendered even if the original form design does not associate it with a data-bound field.

        -  Never omit visible instructional or paragraph text from the form, even if it does not require user input. Preserve the visual order from top to bottom as seen in the source image.

        - Example:
        ```json
        "instructions": {
        "type": "string",
        "title": "Instructions",
        "default": "<p>Please arrive 15 minutes early for your appointment.</p><p>Bring a list of medications and prior reports if available.</p>"
        }


        example ui shcema:
        "instructions": {
        "ui:widget": "HtmlWidget"
        }


        4. TABLES
        - When generating schemas for tables in forms, ensure proper nesting of UI schema components to prevent duplicate keys and ensure correct rendering.
        - Use tables only when necessary.
        - For each array of objects in the schema (e.g. siblings, developmentalConcerns, etc.), include a ui:widget set to "TableHtmlWidget" and define ui:options.inputs as a two-dimensional array with each column represented as an object that includes name (matching the key in the schema) and label (the column title).
        - even if the data is cut off to the next page, the table should be rendered in the same way as it is in the original form.
        Example format for ui:options:

        json
        Copy
        Edit
        {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "name", "label": "Sibling Name(s)" },
                { "name": "relation", "label": "Brother/Sister" },
                { "name": "age", "label": "Age" }
            ]
            ]
        }
        }
        When using the TableHtmlWidget component, ensure you provide the correct structure for the options.inputs property. The widget expects:

        1. The "inputs" property must be a two-dimensional array where the first element contains column definitions.
        2. Each column definition must have "name" and "label" properties.
        3. The "name" must match a property name in your schema.

        Example correct format:
        "fieldName": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "type", "label": "Type" },
                { "name": "dateOfBirth", "label": "Date of Birth" },
                { "name": "occupation", "label": "Occupation" }
            ]
            ]
        }
        }

        IMPORTANT: The "inputs" property MUST be a nested array with exactly one array inside containing column definitions. Don't flatten this structure or the widget will display "No table layout inputs defined."

        For the schema structure, ensure all field properties referenced in the table are properly defined:
        "fieldName": {
        "type": "array",
        "title": "Field Title",
        "items": {
            "type": "object",
            "properties": {
            "type": { "type": "string", "title": "Type" },
            "dateOfBirth": { "type": "string", "title": "Date of Birth" },
            "occupation": { "type": "string", "title": "Occupation" }
            }
        }
        }

        PREDEFINED ROW TABLES (FIXED IDENTITY FIELDS)
            Use this when the table contains specific labeled rows that cannot be added/removed (e.g., "Mother", "Father", "Siblings").
            If any predefined rows are present, the table should be rendered in the same way as it is in the original form .place it under default.

            Example for JSON Schema:

            developmentalConcerns": {
        "type": "array",
        "title": "Please indicate if you have had in the past or currently have any concerns in the following areas of development:",
        "items": {
          "type": "object",
          "properties": {
            "areaOfDevelopment": {
              "type": "string",
              "title": "Area of Development"
            },
            "pastConcern": {
              "type": "boolean",
              "title": "Past Concern"
            },
            "currentConcern": {
              "type": "boolean",
              "title": "Current Concern"
            }
          }
        },
        "default": [
          {
            "areaOfDevelopment": "Motor (e.g., crawling, sitting, walking, running, clumsiness)"
          },
          {
            "areaOfDevelopment": "Self-help (e.g., dressing, toileting)"
          },
          {
            "areaOfDevelopment": "Feeding (e.g., drooling, choking, sensitivity to textures)"
          },
          {
            "areaOfDevelopment": "Early play (e.g., using toys appropriately)"
          }
        ]
      }
            Example for JSON Schema:
            JSON Schema:
            json"familyTable": {
            "type": "object",
            "title": "Family Medical/Psychiatric",
            "properties": {
                "mother": {
                "type": "object",
                "title": "Mother",
                "properties": {
                    "education": { "type": "string", "title": "Highest Educational Level" },
                    "health": { "type": "string", "title": "Health Problems" },
                    "psychiatric": { "type": "string", "title": "Psychiatric" }
                }
                },
                "father": {
                "type": "object",
                "title": "Father",
                "properties": {
                    "education": { "type": "string", "title": "Highest Educational Level" },
                    "health": { "type": "string", "title": "Health Problems" },
                    "psychiatric": { "type": "string", "title": "Psychiatric" }
                }
                },
                "siblings": {
                "type": "array",
                "title": "Siblings",
                "items": {
                    "type": "object",
                    "properties": {
                    "education": { "type": "string", "title": "Highest Educational Level" },
                    "health": { "type": "string", "title": "Health Problems" },
                    "psychiatric": { "type": "string", "title": "Psychiatric" }
                    }
                }
                }
            }
            }
            UI Schema:
            json"familyTable": {
            "ui:widget": "PredefinedTableWidget",
            "ui:options": {
                "layout": "table",
                "identityColumn": "relation",
                "predefinedRows": [
                { "id": "mother", "label": "Mother" },
                { "id": "father", "label": "Father" },
                { "id": "siblings", "label": "Siblings", "isArray": true, "minItems": 3 }
                ],
                "columns": [
                { "name": "relation", "label": "Family Medical/Psychiatric", "readOnly": true },
                { "name": "education", "label": "Highest Educational Level" },
                { "name": "health", "label": "Health Problems" },
                { "name": "psychiatric", "label": "Psychiatric" }
                ]
            }
            }
        - Never use "TableWidget" or any unrecognized widget name.
        - The only supported table widget is: "ui:widget": "TableHtmlWidget"
        - automatically derive the title for tables based on the actual field text. For example, for the question "Do any other family members have speech, language, or related difficulties or disorders?", the title for the related table should be derived from this question, rather than being explicitly specified.
        - STRICTLY maintain the name of the field or title of the table as it is in the original form.

        You may render tables in the following valid ways:

        ---

         A. STATIC TABLES (READ-ONLY)

        Use this when the table is purely informational and not intended for user input.
        example for json schema for table:
        "html_field": {
        "type": "string",
        "title": " ",
        "default": "<table><tr><td>Better pattern</td></tr></table>"
        }


        example for ui schema for table:
        "html_field": {
        "ui:widget": "TableHtmlWidget"
        }

        Notes:
        - Escape all quotes in HTML (" → \\")
        - Remove all line breaks for JSON validity
        - Do not define any `field_key` or inputs for static tables

        ---

        B. FILLABLE TABLES (FIXED ROWS / SINGLE SET)

        Use this when users must fill out a known, fixed set of rows (e.g., family members, education history, health conditions).
        -If any predefined rows are present, the table should be rendered in the same way as it is in the original form.



        JSON Schema

        "html_table": {
        "type": "string",
        "title": " ",
        "default": "<table><thead><tr><th>Name</th><th>Age</th></tr></thead><tbody><tr><td>John</td><td>30</td></tr></tbody></table>"
        }

        UI Schema

        "html_table": {
        "ui:widget": "HtmlWidget"
        }





        JSON Schema

        "medicationTable": {
        "type": "array",
        "title": "Medications",
        "items": {
            "type": "object",
            "properties": {
            "name": { "type": "string", "title": "Medication Name" },
            "dose": { "type": "string", "title": "Dosage" },
            "frequency": { "type": "string", "title": "Frequency" }
            }
        }
        }

        UI Schema

        "medicationTable": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "name", "label": "Medication Name" },
                { "name": "dose", "label": "Dosage" },
                { "name": "frequency", "label": "Frequency" }
            ]
            ]
        }
        }

      json schema example:
        "mother_education": {
        "type": "string",
        "title": "Mother - Highest Educational Level"
        },
        "mother_health": {
        "type": "string",
        "title": "Mother - Health Problems"
        },
        "father_education": {
        "type": "string",
        "title": "Father - Highest Educational Level"
        },
        "father_health": {
        "type": "string",
        "title": "Father - Health Problems"
        }


        ui schema example:
        "family_info_group": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "mother_education", "label": "Mother - Highest Educational Level" },
                { "name": "mother_health", "label": "Mother - Health Problems" }
            ],
            [
                { "name": "father_education", "label": "Father - Highest Educational Level" },
                { "name": "father_health", "label": "Father - Health Problems" }
            ]
            ]
        }
        }

        CRITICAL:
        - Every `name` used in the `inputs` array MUST be defined in `jsonSchema` under the same object with:
        - a matching `"title"`
        - a valid `"type"` (typically `"string"`)

        Example in jsonSchema:

                json schema example:
        "mother_education": {
        "type": "string",
        "title": "Mother - Highest Educational Level"
        },
        "mother_health": {
        "type": "string",
        "title": "Mother - Health Problems"
        },
        "father_education": {
        "type": "string",
        "title": "Father - Highest Educational Level"
        },
        "father_health": {
        "type": "string",
        "title": "Father - Health Problems"
        }


        ui schema example:
        "family_info_group": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "mother_education", "label": "Mother - Highest Educational Level" },
                { "name": "mother_health", "label": "Mother - Health Problems" }
            ],
            [
                { "name": "father_education", "label": "Father - Highest Educational Level" },
                { "name": "father_health", "label": "Father - Health Problems" }
            ]
            ]
        }
        }


        Do not mix static HTML rows and dynamic inputs in the same table block.

        ---

        C. REPEATABLE TABLES (DYNAMIC ROWS)

        Use this when the user must be able to add or remove rows dynamically (e.g., list of medications, travel history, multiple children).

        json schema:
        "medicationTable": {
        "type": "array",
        "title": "Medications",
        "items": {
            "type": "object",
            "properties": {
            "name": { "type": "string", "title": "Medication Name" },
            "dose": { "type": "string", "title": "Dosage" },
            "frequency": { "type": "string", "title": "Frequency" }
            }
        }
        }


        ui schema:
        "medicationTable": {
        "ui:widget": "TableHtmlWidget",
        "ui:options": {
            "inputs": [
            [
                { "name": "name", "label": "Medication Name" },
                { "name": "dose", "label": "Dosage" },
                { "name": "frequency", "label": "Frequency" }
            ]
            ]
        }
        }

        This allows the user to dynamically add or remove rows, with consistent column structure.

        ---

        DO NOT:
        - Use `"TableWidget"` or any unsupported widget name.
        - Combine static and fillable content in the same table.
        - Leave `inputs` undefined or use fields not declared in `jsonSchema`.

        Ensure all repeatable or structured table cells use `type: object` (not just string arrays) and match schema definitions exactly.

        5. FIELD TYPES AND WIDGET MAPPINGS

        -DO not use defualt for multiple choice fields use enum with ui:widget: "checkboxes" or "radio" or "select
        -Avoid using "default" for checkbox or multi-select fields like medicalIssues, as it pre-selects all values when the form loads.
         Instead, let users actively select the options that apply to them to avoid incorrect assumptions or data entry errors.
        - A properly structured "uiSchema" object that correctly references the TableHtmlWidget, including any necessary "options" property with an "inputs" array that defines the table columns
        - A matching "schema" object that defines the data structure, particularly focusing on array type fields with object items that have specific properties
        - Ensure the "inputs" option in the uiSchema is properly structured as an array of column definitions, where each column definition includes at least "name" and "label" properties
        - Include an example of initial form data that matches the schema structure
        - Proper nesting of all properties and objects
        -Strictly do no include  "ui:widget": "checkbox" for type boolean fields.
        - Even if its office or admin use do not ignore that fields too.
        - Include office use fields in json schema.
        - Correct placement of all array item definitions (including enums within items objects, not at the array level)
        - Appropriate type definitions for all properties (string, boolean, array, object, etc.)
        - Descriptive titles and any needed default values
        - Short text input → type: "string", ui:widget: "text"
        - Long text area → type: "string", ui:widget: "textarea"
        - Numbers → type: "integer", ui:widget: "updown"

        - Enumerated options → use "enum" with ui:widget: "select", "radio", or "checkboxes"
        - Repeated sections → type: "array", items: { type: "object", ... }
        - Related fields → group inside type: "object"
        IMPORTANT: For array fields, NEVER use "ui:widget": "textarea" directly.
        Use:
        "uiSchema": {
        "fieldName": {
            "items": {
            "ui:widget": "textarea"
            }
        }
        }
        -For every field that uses "ui:widget": "checkboxes", please ensure that "uniqueItems": true is included to ensure it renders correctly.
        -Stictly do  not use default for type array or object fields.
        6. CONSENT AND AGREEMENT FIELDS
        - Use type: "boolean" with the full agreement sentence as the title.
        - ALWAYS escape quotes in titles and text: "I authorize the \\"Release\\" of records."

        7. CONDITIONAL FIELDS
        - If an enum includes "Other", add a conditional string input using "dependencies".
        - Use ui:order to ensure proper layout.

        8. ERROR CORRECTION
        - Silently fix any typos, missing punctuation, or layout mismatches found in the image.
        - Record all changes in the "corrections" section of the output JSON.

        9. PRESERVATION OF MEDICAL CONTEXT
        - Do not omit, reword, or shorten any medical term or sentence.
        - Treat all text as clinically sensitive — accuracy is mandatory.

        10. FALLBACK INSTRUCTIONS
        - If a section cannot be translated using RJSF widgets, convert it into:
          type: "string", ui:widget: "textarea"

        11. OBJECT FIELD TITLES
        - For every field of type "object" without a title, add a title using its property key converted to Title Case.
        - Example: birthDifficulties → "title": "Birth Difficulties"

        12. JSON VALIDATION CRITICAL
        - Before returning your response, validate that it is proper JSON:
          - Ensure all quotes are properly closed and escaped
          - All brackets and braces must have matching pairs
          - No trailing commas in arrays or objects
          - No single quotes for properties or values, use double quotes only
          - String values must use double quotes and escape internal quotes
          - Verify the response is a single valid JSON object with the 5 required properties
          - All the html content must be rendered only in jsonSchema and uischema should define how to render the html content in the form

        13.ARRAY FIELD WIDGETS (CRITICAL - TO PREVENT ERRORS)

        NEVER apply "ui": "textarea" directly to an array field
        For array fields, widget settings must be applied to the items, not to the array itself:

        INCORRECT (will cause errors):
        json"uiSchema": {
        "myArrayField": {
            "ui:widget": "textarea"  // THIS WILL CAUSE THE ERROR
        }
        }
        CORRECT:
        json"uiSchema": {
        "myArrayField": {
            "items": {
            "ui:widget": "textarea"  // Apply to items, not to the array
            }
        }
        }

        For array fields with object items, apply widgets to the specific properties:

        json"uiSchema": {
        "myArrayField": {
            "items": {
            "someProperty": {
                "ui:widget": "textarea"
            }
            }
        }
        }

        sample output for JSON schema:
        {
        "title": "Employee Information",
        "type": "object",
        "properties": {
            "employees": {
            "type": "array",
            "title": "Employees",
            "items": {
                "type": "object",
                "properties": {
                "name": {
                    "type": "string",
                    "title": "Name"
                },
                "role": {
                    "type": "string",
                    "title": "Role"
                },
                "age": {
                    "type": "number",
                    "title": "Age"
                }
                },
                "required": ["name", "role", "age"]
            }
            }
        }
        }
        sample output for UISchema:
        {
        "employees": {
            "ui:widget": "table"
        }
        }
        sample HTML field for json schema:
        "html_field": {
        "title": " ",
        "default": "[FULL HTML CONTENT]",
        "type": "string"
        }
        sample HTML for UISchema:
        "html_field": {
        "ui:widget": "HtmlWidget"
        }
        14.YES/NO FIELDS (CRITICAL)
            For yes/no questions, ALWAYS use radio buttons with two options, not a single checkbox:
            json"jsonSchema": {
            "hasAllergies": {
                "type": "string",
                "title": "Do you have any allergies?",
                "enum": ["Yes", "No"]
            }
            }
            json"uiSchema": {
            "hasAllergies": {
                "ui:widget": "radio",
                "ui:options": {
                "inline": true
                }
            }
            }

            If the original form shows "Yes/No" options or check boxes for affirmative/negative responses, always implement as radio buttons with "Yes" and "No" values.
            If multiple yes/no options appear in sequence, still implement each as its own radio field.
        15.FORM TITLE :
            - Do not have a separate title field in the json schema.
            - Use the respective title inside the properties of the json schema.
            - If the title need to be included take it from the header of the document like company name.
            - Ensure to keep the table titles,headings,subheadings only inside properties of the json schema.

        16. DOCUMENT STRUCTURE HANDLING
        - IGNORE page numbers, footers, headers, and document reference numbers.
        - DO NOT include page footer text like "Page X of Y" or addresses at the bottom of pages.
        - EXCLUDE any watermarks, form IDs, or revision dates that appear in headers/footers.
        - When content spans multiple pages, maintain logical structure regardless of page breaks.

        17.ACKNOWLEDGEMENTS AND POLICY TEXT
        - PRESERVE acknowledgement text EXACTLY as written - no summarizing or rewording.
        - Do not miss any declaration if present in the document that are crucial to include in the json schema
        - Use the HtmlWidget for policy text and acknowledgements:
            ```json
            "acknowledgement": {
            "type": "string",
            "title": "Acknowledgement",
            "default": "<p>EXACT policy text here...</p>"
            }
            ```
        - In uiSchema:
            ```json
            "acknowledgement": {
            "ui:widget": "HtmlWidget"
            }
                ```


        18. OUTPUT FORMAT
        - Provide only the valid JSON object - no explanations, no markdown.
        - Output must start with '{' and end with '}' - a single complete JSON object.
        - Include all five required sections: jsonSchema, uiSchema, formSchema, widgetSchema, and corrections.
        - The formSchema should be an array, even if empty: []
        - If no corrections, use empty object: "corrections": {}



 """

    return {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "text", "text": f"Page Text:\n{text}"},
                {"type": "text",
                    "text": f"Structured Data (from Textract):\n{json.dumps(structured_data)}"},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64_image
                }}
            ]}
        ],
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8000,
        "temperature": 0.4
    }


def handle_other(json_in):

    prompt = """
You are an AI that extracts schema definitions from document content for rendering with React JSON Schema Form (RJSF).

You are given with a RJSF data, if you find values or option like "Other" or "Others" in any multiple choice question you must dynamically render an input field titled "Please elaborate" **immediately below the related option**, not at the end of the form.

## CRITICAL OUTPUT FORMAT
```json
{
  "jsonSchema": { },
  "uiSchema": { },
  "formSchema": [ ],
  "corrections": { }
}
````

Workflow:

1. Analyze the existing RJSF file.
2. Identify any enum/radio/boolean/object field with option "Other"/"Others".
3. Add a conditional field **immediately after** the related input in order, titled **"Please elaborate"**, shown only when "Other" is selected.
4. Use RJSF dependency patterns (`dependencies.oneOf`) and place `"Please elaborate"` input **inline after the triggering field**.
5. Do not place the field at the bottom of the form.
6. Follow the examples shown below for patterns.
7. Return only the final modified RJSF file in the same structure, **no extra explanation or text.**
8. Do not alter anything else in the original form.
9.Stictly do not remove any field or change the order of the fields in the form for any reason.Your only job is to add the conditional field "Please elaborate" immediately after the related option.
10.If some data is missing in the output json you will be hugely fined and penalized for that.
11.DO not skip any field,input,object,data after the other or please elaborate field.Make sure none of the values from the give input json should not be missed or removed.
12.If you feel it is complex to add the please elaborate fie;d you can shuffle the order to take the other to the bottom of the form and add the "Please elaborate" field immediately after the "Other" option.
**Prompt:**

> *"Design a JSON Schema + UI Schema where a dynamic input field appears when a specific value like 'Other' is selected. Cover different control types: multiple choice (enum with checkboxes), radio buttons (single enum), boolean toggle, and object properties. Ensure that the dynamic input only appears conditionally, and is validated when relevant. Include working example JSON Schema and UI Schema for each case."*

---

## 1. Enum (Multiple Select with "Other")

### JSON Schema

```json
{
  "type": "object",
  "properties": {
    "languages": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": ["English", "Spanish", "Other"]
      },
      "uniqueItems": true
    }
  },
  "dependencies": {
    "languages": {
      "oneOf": [
        {
          "properties": {
            "languages": {
              "contains": { "const": "Other" }
            },
            "languagesOther": {
              "type": "string",
              "title": "Please elaborate"
            }
          },
          "required": ["languagesOther"]
        },
        {
          "properties": {
            "languages": {
              "not": {
                "contains": { "const": "Other" }
              }
            }
          }
        }
      ]
    }
  }
}
```

### UI Schema

```json
{
  "languages": {
    "ui:widget": "checkboxes"
  },
  "languagesOther": {
    "ui:widget": "textarea"
  }
}
```

---

## 2. Enum (Radio / Single Choice)

### JSON Schema

```json
{
  "type": "object",
  "properties": {
    "gender": {
      "type": "string",
      "enum": ["Male", "Female", "Other"]
    }
  },
  "dependencies": {
    "gender": {
      "oneOf": [
        {
          "properties": {
            "gender": { "const": "Other" },
            "genderOther": {
              "type": "string",
              "title": "Please elaborate"
            }
          },
          "required": ["genderOther"]
        },
        {
          "properties": {
            "gender": {
              "enum": ["Male", "Female"]
            }
          }
        }
      ]
    }
  }
}
```

### UI Schema

```json
{
  "gender": {
    "ui:widget": "radio"
  },
  "genderOther": {
    "ui:widget": "text"
  }
}
```

---

## 3. Boolean Toggle with Conditional Text Field

### JSON Schema

```json
{
  "type": "object",
  "properties": {
    "hasAllergies": {
      "type": "boolean",
      "title": "Do you have allergies?"
    }
  },
  "dependencies": {
    "hasAllergies": {
      "oneOf": [
        {
          "properties": {
            "hasAllergies": { "const": true },
            "allergiesDetail": {
              "type": "string",
              "title": "Please elaborate"
            }
          },
          "required": ["allergiesDetail"]
        },
        {
          "properties": {
            "hasAllergies": { "const": false }
          }
        }
      ]
    }
  }
}
```

### UI Schema

```json
{
  "allergiesDetail": {
    "ui:widget": "textarea"
  }
}
```

---

## 4. Nested Object with Boolean Trigger

### JSON Schema

```json
{
  "type": "object",
  "properties": {
    "sensoryProcessing": {
      "type": "object",
      "properties": {
        "sensitiveToNoise": {
          "type": "boolean",
          "title": "Sensitive to noise?"
        }
      },
      "dependencies": {
        "sensitiveToNoise": {
          "oneOf": [
            {
              "properties": {
                "sensitiveToNoise": { "const": true },
                "noiseDetail": {
                  "type": "string",
                  "title": "Please elaborate"
                }
              },
              "required": ["noiseDetail"]
            },
            {
              "properties": {
                "sensitiveToNoise": { "const": false }
              }
            }
          ]
        }
      }
    }
  }
}
```

### UI Schema

```json
{
  "sensoryProcessing": {
    "noiseDetail": {
      "ui:widget": "textarea"
    }
  }
}
```

"""+f"""
Input RJSF:
{json_in}
"""
    return {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": prompt}
            ]}
        ],
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8000,
        "temperature": 0.4
    }