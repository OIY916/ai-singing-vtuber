# Speech Recognition (MiMo-V2.5-ASR)

{/* feishu-style:text-align:left */}
Speech recognition converts input audio into text output, suitable for meeting transcription, lyrics recognition, dialect transcription, noisy environment recordings, and more. You can improve recognition accuracy by specifying language parameters.

{/* feishu-style:text-align:left */}
**Core Capabilities**

- **Broad Language and Dialect Coverage**: Supports bilingual Chinese-English recognition with automatic language detection. Natively recognizes Cantonese, Wu, Minnan, Sichuan, and other Chinese dialects.

- **Robust in Complex Scenarios**: Maintains stable recognition in noisy environments, far-field pickup, and multi-speaker overlapping conversations. Also supports lyrics transcription with background music.

- **Precise Handling of Specialized Content**: Accurately recognizes knowledge-intensive content such as classical poetry, technical terminology, proper nouns, and place names. Automatically generates punctuation without post-processing.

## Supported Models

{/* feishu-style:text-align:left */}
Currently, only the `mimo-v2.5-asr` model is supported.

## Prerequisites

{/* feishu-style:text-align:left */}
For API Key setup and other prerequisites, please refer to [First API Call](https://platform.xiaomimimo.com/#/docs/quick-start/first-api-call).

## Supported Audio Formats

{/* feishu-style:text-align:left */}
Currently, only `wav` and `mp3` audio sample files are supported. Before passing audio to the API, convert the file to a Base64 encoded string. The encoded string size must not exceed 10 MB. Currently, two audio input methods are supported:

<Tab>
  <TabItem label={`Data URL`}>

```json
"input_audio": {
    "data": "data:{MIME_TYPE};base64,$BASE64_AUDIO"
}
```

  </TabItem>
  <TabItem label={`Pure Base64 Encoding`}>

{/* feishu-style:text-align:left */}
When passing audio in pure Base64 encoding, the `format` field must be passed simultaneously to specify the audio format.

```json
"input_audio": {
    "data": "$BASE64_AUDIO",
    "format": "{format}"
}
```

  </TabItem>
</Tab>

{/* feishu-style:text-align:left */}
**Supported formats and their MIME types:** 

<table>
<colgroup>
<col style="width: 50%" />
<col style="width: 50%" />
</colgroup>
<thead>
<tr>
<th><span style="display: inline-block; width: 100%; text-align: left;">Format</span></th>
<th><span style="display: inline-block; width: 100%; text-align: left;">MIME Type</span></th>
</tr>
</thead>
<tbody>
<tr>
<td><span style="display: inline-block; width: 100%; text-align: left;">wav</span></td>
<td><span style="display: inline-block; width: 100%; text-align: left;">`audio/wav`</span></td>
</tr>
<tr>
<td><span style="display: inline-block; width: 100%; text-align: left;">mp3</span></td>
<td><span style="display: inline-block; width: 100%; text-align: left;">`audio/mpeg` or `audio/mp3`</span></td>
</tr>
</tbody>
</table>

## Code Sample

<div className='mdx-highlight mdx-highlight-info' data-highlight-icon='info'>

Set `asr_options.language` to specify the language. Auto detection will be used if this parameter is not configured. Manual specification is recommended when the language is confirmed to improve recognition performance. Supported values: `auto`, `zh`, `en`.

</div>

### Non-streaming Call

<Tab>
  <TabItem label={`Python SDK`}>

```python
import os
import base64
from openai import OpenAI

client = OpenAI(
    api_key=os.environ.get("MIMO_API_KEY"),
    base_url="https://api.xiaomimimo.com/v1"
)

# Replace with the actual local file path
with open("audio_file.wav", "rb") as f:
    audio_bytes = f.read()
audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

completion = client.chat.completions.create(
    model="mimo-v2.5-asr",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": f"data:audio/wav;base64,{audio_base64}"
                    }
                }
            ]
        }
    ],
    extra_body={
        "asr_options": {
            "language": "en"
        }
    }
)

print(completion.model_dump_json())
```

  </TabItem>
  <TabItem label={`Curl`}>

```bash
curl --location --request POST 'https://api.xiaomimimo.com/v1/chat/completions' \
--header "api-key: $MIMO_API_KEY" \
--header 'Content-Type: application/json' \
--data-raw '{
    "model": "mimo-v2.5-asr",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": "data:{MIME_TYPE};base64,$BASE64_AUDIO"
                    }
                }
            ]
        }
    ],
    "asr_options": {
        "language": "en"
    }
}'
```

  </TabItem>
</Tab>

### Streaming Call

<Tab>
  <TabItem label={`Python SDK`}>

```python
import os
import base64
from openai import OpenAI

client = OpenAI(
    api_key=os.environ.get("MIMO_API_KEY"),
    base_url="https://api.xiaomimimo.com/v1"
)

# Replace with the actual local file path
with open("audio_file.wav", "rb") as f:
    audio_bytes = f.read()
audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

completion = client.chat.completions.create(
    model="mimo-v2.5-asr",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": f"data:audio/wav;base64,{audio_base64}"
                    }
                }
            ]
        }
    ],
    extra_body={
        "asr_options": {
            "language": "auto"
        }
    },
    stream=True
)

for chunk in completion:
    print(chunk.model_dump_json())
```

  </TabItem>
  <TabItem label={`Curl`}>

```bash
curl --location --request POST 'https://api.xiaomimimo.com/v1/chat/completions' \
--header "api-key: $MIMO_API_KEY" \
--header 'Content-Type: application/json' \
--data-raw '{
    "model": "mimo-v2.5-asr",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": "data:{MIME_TYPE};base64,$BASE64_AUDIO"
                    }
                }
            ]
        }
    ],
    "asr_options": {
        "language": "auto"
    },
    "stream": true
}'
```

  </TabItem>
</Tab>

## Price

- Billing: Please refer to [Pay‑As‑You‑Go API](https://platform.xiaomimimo.com/#/docs/pricing).

- View Bill: You can view your usage on the [Billing](https://platform.xiaomimimo.com/#/console/usage) page in the Console.
