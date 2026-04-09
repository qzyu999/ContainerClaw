[Video](https://www.youtube.com/watch?v=jZVBoFOJK-Q)

Gemma is a family of generative artificial intelligence models and you can
use them in a wide variety of generation tasks, including question answering,
summarization, and reasoning. Gemma models are provided with open weights and
permit responsible
[commercial use](https://ai.google.dev/gemma/terms),
allowing you to tune and deploy them in your own projects and applications.

Gemma 4 model family spans three distinct architectures tailored for specific
hardware requirements:

- **Small Sizes:** 2B and 4B effective parameter models built for ultra-mobile, edge, and browser deployment (e.g., Pixel, Chrome).
- **Dense:** A powerful 31B parameter dense model that bridges the gap between server-grade performance and local execution.
- **Mixture-of-Experts:** A highly efficient 26B MoE model designed for high-throughput, advanced reasoning.

You can download Gemma 4 models from
[Kaggle](https://www.kaggle.com/models?query=gemma-4&publisher=google) and
[Hugging Face](https://huggingface.co/collections/google/gemma-4).
For more technical details on Gemma 4, see the
[Model Card](https://ai.google.dev/gemma/docs/core/model_card_4).
Earlier versions of Gemma core models are also available for download. For more
information, see [Previous Gemma models](https://ai.google.dev/gemma/docs/core#previous-models).

[Get it on Kaggle](https://www.kaggle.com/models?query=gemma-4&publisher=google)
[Get it on Hugging Face](https://huggingface.co/collections/google/gemma-4)

## Capabilities

- **Reasoning:** All models in the family are designed as highly capable reasoners, with configurable [thinking
  modes](https://ai.google.dev/gemma/docs/capabilities/thinking).
- **Extended Multimodalities:** Processes Text, [Image](https://ai.google.dev/gemma/docs/capabilities/vision/image) with variable aspect ratio and resolution support (all models), [Video](https://ai.google.dev/gemma/docs/capabilities/vision/video), and [Audio](https://ai.google.dev/gemma/docs/capabilities/audio) (featured natively on the E2B and E4B models).
- **Increased Context Window:** Small models feature a 128K context window, while the medium models support 256K.
- **Enhanced Coding \& Agentic Capabilities:** Achieves notable improvements in coding benchmarks alongside built-in [function-calling
  support](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4), powering highly capable autonomous agents.
- **Native System Prompt Support:** Gemma 4 introduces built-in support for the system role, enabling more structured and controllable conversations.

## Parameter sizes and quantization

Gemma 4 models are available in 4 parameter sizes: E2B, E4B, 31B and 26B A4B.
The models can be used with their default precision (16-bit) or with a lower
precision using quantization. The different sizes and precisions represent a set
of trade-offs for your AI application. Models with higher parameters and bit
counts (higher precision) are generally more capable, but are more expensive to
run in terms of processing cycles, memory cost and power consumption. Models
with lower parameters and bit counts (lower precision) have less capabilities,
but may be sufficient for your AI task.

### Gemma 4 Inference Memory Requirements

The following table details the approximate GPU or TPU memory requirements for
running inference with each size of the Gemma 4 model versions.

> [!NOTE]
> **Note:** These numbers may change based on your specific inference tool and environment.

| Parameters | BF16 (16-bit) | SFP8 (8-bit) | Q4_0 (4-bit) |
|---|---|---|---|
| Gemma 4 E2B | 9.6 GB | 4.6 GB | 3.2 GB |
| Gemma 4 E4B | 15 GB | 7.5 GB | 5 GB |
| Gemma 4 31B | 58.3 GB | 30.4 GB | 17.4 GB |
| Gemma 4 26B A4B | 48 GB | 25 GB | 15.6 GB |

**Table 1.** Approximate GPU or TPU memory required to load Gemma 4 models based
on parameter count and quantization level.

### Key Considerations for Memory Planning

- **Efficient Architecture (E2B and E4B):** The "E" stands for "effective" parameters. The smaller models incorporate Per-Layer Embeddings (PLE) to maximize parameter efficiency in on-device deployments. Rather than adding more layers to the model, PLE gives each decoder layer its own small embedding for every token. These embedding tables are large but only used for quick lookups, which is why the total memory required to load static weights is higher than the effective parameter count suggests.
- **The MoE Architecture (26B A4B):** The 26B is a Mixture of Experts model. While it only activates 4 billion parameters per token during generation, **all 26 billion parameters** must be loaded into memory to maintain fast routing and inference speeds. This is why its baseline memory requirement is much closer to a dense 26B model than a 4B model.
- **Base Weights Only:** The estimates in the preceding table *only* account for the memory required to load the static model weights. They don't include the additional VRAM needed for supporting software or the context window.
- **Context Window (KV Cache):** Memory consumption will increase dynamically based on the total number of tokens in your prompt and the generated response. Larger context windows require significantly more VRAM on top of the base model weights.
- **Fine-Tuning Overhead:** Memory requirements for *fine-tuning* Gemma models are drastically higher than for standard inference. Your exact footprint will depend heavily on the development framework, batch size, and whether you are using full-precision tuning versus a Parameter-Efficient Fine-Tuning (PEFT) method like Low-Rank Adaptation (LoRA).

## Previous Gemma models

You can work with previous generations of Gemma models, which are also
available from [Kaggle](https://www.kaggle.com/models?query=gemma) and
[Hugging Face](https://huggingface.co/google/collections).
For more technical details about previous Gemma models, see the following
model card pages:

- Gemma 3 [Model Card](https://ai.google.dev/gemma/docs/core/model_card_3)
- Gemma 2 [Model Card](https://ai.google.dev/gemma/docs/core/model_card_2)
- Gemma 1 [Model Card](https://ai.google.dev/gemma/docs/core/model_card)

Ready to start building?
[Get started](https://ai.google.dev/gemma/docs/get_started)
with Gemma models!

![Gemma 4 Banner](https://ai.google.dev/static/gemma/images/gemma4_banner.png)


[Hugging Face](https://huggingface.co/collections/google/gemma-4) \|
[GitHub](https://github.com/google-gemma) \|
[Launch Blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/) \|
[Documentation](https://ai.google.dev/gemma/docs/core)


**License** : [Apache 2.0](https://ai.google.dev/gemma/docs/gemma_4_license) \| **Authors** : [Google DeepMind](https://deepmind.google/models/gemma/)

Gemma is a family of open models built by Google DeepMind. Gemma 4 models are
multimodal, handling text and image input (with audio supported on small models)
and generating text output. This release includes open-weights models in both
pre-trained and instruction-tuned variants. Gemma 4 features a context window of
up to 256K tokens and maintains multilingual support in over 140 languages.

Featuring both Dense and Mixture-of-Experts (MoE) architectures, Gemma 4 is
well-suited for tasks like text generation, coding, and reasoning. The models
are available in four distinct sizes: **E2B** , **E4B** , **26B A4B** , and
**31B**. Their diverse sizes make them deployable in environments ranging from
high-end phones to laptops and servers, democratizing access to state-of-the-art
AI.

Gemma 4 introduces key **capability and architectural advancements**:

- **Reasoning** -- All models in the family are designed as highly capable
  reasoners, with configurable thinking modes.

- **Extended Multimodalities** -- Processes Text, Image with variable aspect
  ratio and resolution support (all models), Video, and Audio (featured
  natively on the E2B and E4B models).

- **Diverse \& Efficient Architectures** -- Offers Dense and Mixture-of-Experts
  (MoE) variants of different sizes for scalable deployment.

- **Optimized for On-Device** -- Smaller models are specifically designed for
  efficient local execution on laptops and mobile devices.

- **Increased Context Window** -- The small models feature a 128K context
  window, while the medium models support 256K.

- **Enhanced Coding \& Agentic Capabilities** -- Achieves notable improvements
  in coding benchmarks alongside native function-calling support, powering
  highly capable autonomous agents.

- **Native System Prompt Support** -- Gemma 4 introduces native support for the
  `system` role, enabling more structured and controllable conversations.

## **Models Overview**

Gemma 4 models are designed to deliver frontier-level performance at each size,
targeting deployment scenarios from mobile and edge devices (E2B, E4B) to
consumer GPUs and workstations (26B A4B, 31B). They are well-suited for
reasoning, agentic workflows, coding, and multimodal understanding.

The models employ a hybrid attention mechanism that interleaves local sliding
window attention with full global attention, ensuring the final layer is always
global. This hybrid design delivers the processing speed and low memory
footprint of a lightweight model without sacrificing the deep awareness required
for complex, long-context tasks. To optimize memory for long contexts, global
layers feature unified Keys and Values, and apply Proportional RoPE (p-RoPE).

### Dense Models

| Property | E2B | E4B | 31B Dense |
|---|---|---|---|
| **Total Parameters** | 2.3B effective (5.1B with embeddings) | 4.5B effective (8B with embeddings) | 30.7B |
| **Layers** | 35 | 42 | 60 |
| **Sliding Window** | 512 tokens | 512 tokens | 1024 tokens |
| **Context Length** | 128K tokens | 128K tokens | 256K tokens |
| **Vocabulary Size** | 262K | 262K | 262K |
| **Supported Modalities** | Text, Image, Audio | Text, Image, Audio | Text, Image |
| **Vision Encoder Parameters** | *\~150M* | *\~150M* | *\~550M* |
| **Audio Encoder Parameters** | *\~300M* | *\~300M* | No Audio |

The "E" in E2B and E4B stands for "effective" parameters. The smaller models
incorporate Per-Layer Embeddings (PLE) to maximize parameter efficiency in
on-device deployments. Rather than adding more layers or parameters to the
model, PLE gives each decoder layer its own small embedding for every token.
These embedding tables are large but are only used for quick lookups, which is
why the effective parameter count is much smaller than the total.

### Mixture-of-Experts (MoE) Model

| Property | 26B A4B MoE |
|---|---|
| **Total Parameters** | 25.2B |
| **Active Parameters** | 3.8B |
| **Layers** | 30 |
| **Sliding Window** | 1024 tokens |
| **Context Length** | 256K tokens |
| **Vocabulary Size** | 262K |
| **Expert Count** | 8 active / 128 total and 1 shared |
| **Supported Modalities** | Text, Image |
| **Vision Encoder Parameters** | *\~550M* |

The "A" in 26B A4B stands for "active parameters" in contrast to the total
number of parameters the model contains. By only activating a 4B subset of
parameters during inference, the Mixture-of-Experts model runs much faster than
its 26B total might suggest. This makes it an excellent choice for fast
inference compared to the dense 31B model since it runs almost as fast as a
4B-parameter model.

## **Benchmark Results**

These models were evaluated against a large collection of different datasets and
metrics to cover different aspects of text generation. Evaluation results marked
in the table are for instruction-tuned models.

|   | Gemma 4 31B | Gemma 4 26B A4B | Gemma 4 E4B | Gemma 4 E2B | Gemma 3 27B (no think) |
|---|---|---|---|---|---|
| MMLU Pro | 85.2% | 82.6% | 69.4% | 60.0% | 67.6% |
| AIME 2026 no tools | 89.2% | 88.3% | 42.5% | 37.5% | 20.8% |
| LiveCodeBench v6 | 80.0% | 77.1% | 52.0% | 44.0% | 29.1% |
| Codeforces ELO | 2150 | 1718 | 940 | 633 | 110 |
| GPQA Diamond | 84.3% | 82.3% | 58.6% | 43.4% | 42.4% |
| Tau2 (average over 3) | 76.9% | 68.2% | 42.2% | 24.5% | 16.2% |
| HLE no tools | 19.5% | 8.7% | - | - | - |
| HLE with search | 26.5% | 17.2% | - | - | - |
| BigBench Extra Hard | 74.4% | 64.8% | 33.1% | 21.9% | 19.3% |
| MMMLU | 88.4% | 86.3% | 76.6% | 67.4% | 70.7% |
| **Vision** |   |   |   |   |   |
| MMMU Pro | 76.9% | 73.8% | 52.6% | 44.2% | 49.7% |
| OmniDocBench 1.5 (average edit distance, lower is better) | 0.131 | 0.149 | 0.181 | 0.290 | 0.365 |
| MATH-Vision | 85.6% | 82.4% | 59.5% | 52.4% | 46.0% |
| MedXPertQA MM | 61.3% | 58.1% | 28.7% | 23.5% | - |
| **Audio** |   |   |   |   |   |
| CoVoST | - | - | 35.54 | 33.47 | - |
| FLEURS (lower is better) | - | - | 0.08 | 0.09 | - |
| **Long Context** |   |   |   |   |   |
| MRCR v2 8 needle 128k (average) | 66.4% | 44.1% | 25.4% | 19.1% | 13.5% |

## **Core Capabilities**

Gemma 4 models handle a broad range of tasks across text, vision, and audio. Key
capabilities include:

- **Thinking** -- Built-in reasoning mode that lets the model think step-by-step before answering.
- **Long Context** -- Context windows of up to 128K tokens (E2B/E4B) and 256K tokens (26B A4B/31B).
- **Image Understanding** -- Object detection, Document/PDF parsing, screen and UI understanding, chart comprehension, OCR (including multilingual), handwriting recognition, and pointing. Images can be processed at variable aspect ratios and resolutions.
- **Video Understanding** -- Analyze video by processing sequences of frames.
- **Interleaved Multimodal Input** -- Freely mix text and images in any order within a single prompt.
- **Function Calling** -- Native support for structured tool use, enabling agentic workflows.
- **Coding** -- Code generation, completion, and correction.
- **Multilingual** -- Out-of-the-box support for 35+ languages, pre-trained on 140+ languages.
- **Audio** (E2B and E4B only) -- Automatic speech recognition (ASR) and speech-to-translated-text translation across multiple languages.

## Getting Started

You can use all Gemma 4 models with the latest version of Transformers. To get
started, install the necessary dependencies in your environment:

`pip install -U transformers torch accelerate`

Once you have everything installed, you can proceed to load the model with the
code below:

    import torch
    from transformers import AutoProcessor, AutoModelForCausalLM

    MODEL_ID = "google/gemma-4-E2B-it"

    # Load model
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        dtype=torch.bfloat16,
        device_map="auto"
    )

Once the model is loaded, you can start generating output:

    # Prompt
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Write a short joke about saving RAM."},
    ]

    # Process input
    text = processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False
    )
    inputs = processor(text=text, return_tensors="pt").to(model.device)
    input_len = inputs["input_ids"].shape[-1]

    # Generate output
    outputs = model.generate(**inputs, max_new_tokens=1024)
    response = processor.decode(outputs[0][input_len:], skip_special_tokens=False)

    # Parse thinking
    processor.parse_response(response)

To enable reasoning, set `enable_thinking=True` and the `parse_response`
function will take care of parsing the thinking output.

## **Best Practices**

For the best performance, use these configurations and best practices:

### 1. Sampling Parameters

Use the following standardized sampling configuration across all use cases:

- `temperature=1.0`
- `top_p=0.95`
- `top_k=64`

### 2. Thinking Mode Configuration

Compared to Gemma 3, the models use standard `system`, `assistant`, and `user`
roles. To properly manage the thinking process, use the following control
tokens:

- **Trigger Thinking:** Thinking is enabled by including the `<|think|>` token at the start of the system prompt. To disable thinking, remove the token.
- **Standard Generation:** When thinking is enabled, the model will output its internal reasoning followed by the final answer using this structure: `<|channel>thought\n`**\[Internal reasoning\]** `<channel|>`
- **Disabled Thinking Behavior:** For all models except for the E2B and E4B variants, if thinking is disabled, the model will still generate the tags but with an empty thought block: `<|channel>thought\n<channel|>`**\[Final
  answer\]**

> Note that many libraries like Transformers and llama.cpp handle the
> complexities of the chat template for you.

### 3. Multi-Turn Conversations

- **No Thinking Content in History** : In multi-turn conversations, the historical model output should only include the final response. Thoughts from previous model turns must *not be added* before the next user turn begins.

### 4. Modality order

- For optimal performance with multimodal inputs, place image and/or audio content **before** the text in your prompt.

### 5. Variable Image Resolution

Aside from variable aspect ratios, Gemma 4 supports variable image resolution
through a configurable visual token budget, which controls how many tokens are
used to represent an image. A higher token budget preserves more visual detail
at the cost of additional compute, while a lower budget enables faster inference
for tasks that don't require fine-grained understanding.

- The supported token budgets are: **70** , **140** , **280** , **560** , and **1120** .
  - Use *lower budgets* for classification, captioning, or video understanding, where faster inference and processing many frames outweigh fine-grained detail.
  - Use *higher budgets* for tasks like OCR, document parsing, or reading small text.

### 6. Audio

Use the following prompt structures for audio processing:

- **Audio Speech Recognition (ASR)**

    Transcribe the following speech segment in {LANGUAGE} into {LANGUAGE} text.

    Follow these specific instructions for formatting the answer:
    *   Only output the transcription, with no newlines.
    *   When transcribing numbers, write the digits, i.e. write 1.7 and not one point seven, and write 3 instead of three.

- **Automatic Speech Translation (AST)**

    Transcribe the following speech segment in {SOURCE_LANGUAGE}, then translate it into {TARGET_LANGUAGE}.
    When formatting the answer, first output the transcription in {SOURCE_LANGUAGE}, then one newline, then output the string '{TARGET_LANGUAGE}: ', then the translation in {TARGET_LANGUAGE}.

### 7. Audio and Video Length

All models support image inputs and can process videos as frames whereas the E2B
and E4B models also support audio inputs. Audio supports a maximum length of 30
seconds. Video supports a maximum of 60 seconds assuming the images are
processed at one frame per second.

## **Model Data**

Data used for model training and how the data was processed.

### **Training Dataset**

Our pre-training dataset is a large-scale, diverse collection of data
encompassing a wide range of domains and modalities, which includes web
documents, code, images, audio, with a cutoff date of January 2025. Here are the
key components:

- **Web Documents**: A diverse collection of web text ensures the model is exposed to a broad range of linguistic styles, topics, and vocabulary. The training dataset includes content in over 140 languages.
- **Code**: Exposing the model to code helps it to learn the syntax and patterns of programming languages, which improves its ability to generate code and understand code-related questions.
- **Mathematics**: Training on mathematical text helps the model learn logical reasoning, symbolic representation, and to address mathematical queries.
- **Images**: A wide range of images enables the model to perform image analysis and visual data extraction tasks.

The combination of these diverse data sources is crucial for training a powerful
multimodal model that can handle a wide variety of different tasks and data
formats.

### **Data Preprocessing**

Here are the key data cleaning and filtering methods applied to the training
data:

- **CSAM Filtering**: Rigorous CSAM (Child Sexual Abuse Material) filtering was applied at multiple stages in the data preparation process to ensure the exclusion of harmful and illegal content.
- **Sensitive Data Filtering**: As part of making Gemma pre-trained models safe and reliable, automated techniques were used to filter out certain personal information and other sensitive data from training sets.
- **Additional methods** : Filtering based on content quality and safety in line with [our
  policies](https://ai.google/static/documents/ai-responsibility-update-published-february-2025.pdf).

## **Ethics and Safety**

As open models become central to enterprise infrastructure, provenance and
security are paramount. Developed by Google DeepMind, Gemma 4 undergoes the same
rigorous safety evaluations as our proprietary Gemini models.

### **Evaluation Approach**

Gemma 4 models were developed in partnership with internal safety and
responsible AI teams. A range of automated as well as human evaluations were
conducted to help improve model safety. These evaluations align with [Google's
AI principles](https://ai.google/principles/), as well as safety policies, which
aim to prevent our generative AI models from generating harmful content,
including:

- Content related to child sexual abuse material and exploitation
- Dangerous content (e.g., promoting suicide, or instructing in activities that could cause real-world harm)
- Sexually explicit content
- Hate speech (e.g., dehumanizing members of protected groups)
- Harassment (e.g., encouraging violence against people)

### **Evaluation Results**

For all areas of safety testing, we saw major improvements in all categories of
content safety relative to previous Gemma models. Overall, Gemma 4 models
significantly outperform Gemma 3 and 3n models in improving safety, while
keeping unjustified refusals low. All testing was conducted without safety
filters to evaluate the model capabilities and behaviors. For both text-to-text
and image-to-text, and across all model sizes, the model produced minimal policy
violations, and showed significant improvements over previous Gemma models'
performance.

## **Usage and Limitations**

These models have certain limitations that users should be aware of.

### **Intended Usage**

Multimodal models (capable of processing vision, language, and/or audio) have a
wide range of applications across various industries and domains. The following
list of potential uses is not comprehensive. The purpose of this list is to
provide contextual information about the possible use-cases that the model
creators considered as part of model training and development.

- **Content Creation and Communication**
  - **Text Generation**: These models can be used to generate creative text formats such as poems, scripts, code, marketing copy, and email drafts.
  - **Chatbots and Conversational AI**: Power conversational interfaces for customer service, virtual assistants, or interactive applications.
  - **Text Summarization**: Generate concise summaries of a text corpus, research papers, or reports.
  - **Image Data Extraction**: These models can be used to extract, interpret, and summarize visual data for text communications.
  - **Audio Processing and Interaction**: The smaller models (E2B and E4B) can analyze and interpret audio inputs, enabling voice-driven interactions and transcriptions.
- **Research and Education**
  - **Natural Language Processing (NLP) and VLM Research**: These models can serve as a foundation for researchers to experiment with VLM and NLP techniques, develop algorithms, and contribute to the advancement of the field.
  - **Language Learning Tools** : Support interactive language learning experiences, aiding in grammar correction or providing writing practice.
    - **Knowledge Exploration**: Assist researchers in exploring large bodies of text by generating summaries or answering questions about specific topics.

### **Limitations**

- **Training Data**
  - The quality and diversity of the training data significantly influence the model's capabilities. Biases or gaps in the training data can lead to limitations in the model's responses.
  - The scope of the training dataset determines the subject areas the model can handle effectively.
- **Context and Task Complexity**
  - Models perform well on tasks that can be framed with clear prompts and instructions. Open-ended or highly complex tasks might be challenging.
  - A model's performance can be influenced by the amount of context provided (longer context generally leads to better outputs, up to a certain point).
- **Language Ambiguity and Nuance**
  - Natural language is inherently complex. Models might struggle to grasp subtle nuances, sarcasm, or figurative language.
- **Factual Accuracy**
  - Models generate responses based on information they learned from their training datasets, but they are not knowledge bases. They may generate incorrect or outdated factual statements.
- **Common Sense**
  - Models rely on statistical patterns in language. They might lack the ability to apply common sense reasoning in certain situations.

### **Ethical Considerations and Risks**

The development of vision-language models (VLMs) raises several ethical
concerns. In creating an open model, we have carefully considered the following:

- **Bias and Fairness**
  - VLMs trained on large-scale, real-world text and image data can reflect socio-cultural biases embedded in the training material. Gemma 4 models underwent careful scrutiny, input data pre-processing, and post-training evaluations as reported in this card to help mitigate the risk of these biases.
- **Misinformation and Misuse**
  - VLMs can be misused to generate text that is false, misleading, or harmful.
  - Guidelines are provided for responsible use with the model, see the [Responsible Generative AI Toolkit](https://ai.google.dev/responsible).
- **Transparency and Accountability**
  - This model card summarizes details on the models' architecture, capabilities, limitations, and evaluation processes.
  - A responsibly developed open model offers the opportunity to share innovation by making VLM technology accessible to developers and researchers across the AI ecosystem.

**Risks identified and mitigations**:

- **Generation of harmful content**: Mechanisms and guidelines for content safety are essential. Developers are encouraged to exercise caution and implement appropriate content safety safeguards based on their specific product policies and application use cases.
- **Misuse for malicious purposes**: Technical limitations and developer and end-user education can help mitigate against malicious applications of VLMs. Educational resources and reporting mechanisms for users to flag misuse are provided.
- **Privacy violations**: Models were trained on data filtered for removal of certain personal information and other sensitive data. Developers are encouraged to adhere to privacy regulations with privacy-preserving techniques.
- **Perpetuation of biases**: It's encouraged to perform continuous monitoring (using evaluation metrics, human review) and the exploration of de-biasing techniques during model training, fine-tuning, and other use cases.

### **Benefits**

At the time of release, this family of models provides high-performance open
vision-language model implementations designed from the ground up for
responsible AI development compared to similarly sized models.

|---|---|---|---|---|
| [![](https://ai.google.dev/static/site-assets/images/docs/notebook-site-button.png)View on ai.google.dev](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4) | [![](https://www.tensorflow.org/images/colab_logo_32px.png)Run in Google Colab](https://colab.research.google.com/github/google-gemma/cookbook/blob/main/docs/capabilities/text/function-calling-gemma4.ipynb) | [![](https://www.kaggle.com/static/images/logos/kaggle-logo-transparent-300.png)Run in Kaggle](https://kaggle.com/kernels/welcome?src=https://github.com/google-gemma/cookbook/blob/main/docs/capabilities/text/function-calling-gemma4.ipynb) | [![](https://ai.google.dev/images/cloud-icon.svg)Open in Vertex AI](https://console.cloud.google.com/vertex-ai/colab/import/https%3A%2F%2Fraw.githubusercontent.com%2Fgoogle-gemma%2Fcookbook%2Fmain%2Fdocs%2Fcapabilities%2Ftext%2Ffunction-calling-gemma4.ipynb) | [![](https://www.tensorflow.org/images/GitHub-Mark-32px.png)View source on GitHub](https://github.com/google-gemma/cookbook/blob/main/docs/capabilities/text/function-calling-gemma4.ipynb) |

When using a generative artificial intelligence (AI) model such as Gemma, you
may want to use the model to operate programming interfaces in order to complete
tasks or answer questions. Instructing a model by defining a programming
interface and then making a request that uses that interface is called *function
calling*.
>
> > [!IMPORTANT]
> > **Important:** *A Gemma model cannot execute code on its own.* When you generate code with function calling, you must run the generated code yourself or run it as part of your application. Always put safeguards in place to validate any generated code before executing it.
>
This guide shows the process of using Gemma 4 within the Hugging Face ecosystem.

This notebook will run on T4 GPU.

## Install Python packages

Install the Hugging Face libraries required for running the Gemma model and making requests.

    # Install PyTorch & other libraries
    pip install torch accelerate

    # Install the transformers library
    pip install transformers

## Load Model

Use the `transformers` libraries to create an instance of a `processor` and `model` using the `AutoProcessor` and `AutoModelForImageTextToText` classes as shown in the following code example:

    MODEL_ID = "google/gemma-4-E2B-it" # @param ["google/gemma-4-E2B-it","google/gemma-4-E4B-it", "google/gemma-4-31B-it", "google/gemma-4-26B-A4B-it"]

    from transformers import AutoProcessor, AutoModelForMultimodalLM

    model = AutoModelForMultimodalLM.from_pretrained(MODEL_ID, dtype="auto", device_map="auto")
    processor = AutoProcessor.from_pretrained(MODEL_ID)

```
Loading weights:   0%|          | 0/2011 [00:00<?, ?it/s]
```

## Passing Tools

You can pass tools to the model using the `apply_chat_template()` function via the `tools` argument. There are two methods for defining these tools:

- **JSON schema**: You can manually construct a JSON dictionary defining the function name, description, and parameters (including types and required fields).
- **Raw Python Functions** : You can pass actual Python functions. The system automatically generates the required JSON schema by parsing the function's type hints, arguments, and docstrings. For best results, docstrings should adhere to the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings).

Below is the example with the JSON schema.

    from transformers import TextStreamer

    weather_function_schema = {
        "type": "function",
        "function": {
            "name": "get_current_temperature",
            "description": "Gets the current temperature for a given location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city name, e.g. San Francisco",
                    },
                },
                "required": ["location"],
            },
        }
    }

    message = [
        {
            "role": "system", "content": "You are a helpful assistant."
        },
        {
            "role": "user", "content": "What's the temperature in London?"
        }
    ]

    text = processor.apply_chat_template(message, tools=[weather_function_schema], tokenize=False, add_generation_prompt=True)
    inputs = processor(text=text, return_tensors="pt").to(model.device)
    streamer = TextStreamer(processor)
    outputs = model.generate(**inputs, streamer=streamer, max_new_tokens=64)

```
<bos><|turn>system
You are a helpful assistant.<|tool>declaration:get_current_temperature{description:<|"|>Gets the current temperature for a given location.<|"|>,parameters:{properties:{location:{description:<|"|>The city name, e.g. San Francisco<|"|>,type:<|"|>STRING<|"|>} },required:[<|"|>location<|"|>],type:<|"|>OBJECT<|"|>} }<tool|><turn|>
<|turn>user
What's the temperature in London?<turn|>
<|turn>model
<|tool_call>call:get_current_temperature{location:<|"|>London<|"|>}<tool_call|><|tool_response>
```

And the same example with the raw Python function.

    from transformers.utils import get_json_schema

    def get_current_temperature(location: str):
        """
        Gets the current temperature for a given location.

        Args:
            location: The city name, e.g. San Francisco
        """
        return "15°C"

    message = [
        {
            "role": "user", "content": "What's the temperature in London?"
        }
    ]

    text = processor.apply_chat_template(message, tools=[get_json_schema(get_current_temperature)], tokenize=False, add_generation_prompt=True)
    inputs = processor(text=text, return_tensors="pt").to(model.device)
    streamer = TextStreamer(processor)
    outputs = model.generate(**inputs, streamer=streamer, max_new_tokens=256)

```
<bos><|turn>system
<|tool>declaration:get_current_temperature{description:<|"|>Gets the current temperature for a given location.<|"|>,parameters:{properties:{location:{description:<|"|>The city name, e.g. San Francisco<|"|>,type:<|"|>STRING<|"|>} },required:[<|"|>location<|"|>],type:<|"|>OBJECT<|"|>} }<tool|><turn|>
<|turn>user
What's the temperature in London?<turn|>
<|turn>model
<|tool_call>call:get_current_temperature{location:<|"|>London<|"|>}<tool_call|><|tool_response>
```

## Full function calling sequence

This section demonstrates a three-stage cycle for connecting the model to external tools: the **Model's Turn** to generate function call objects, the **Developer's Turn** to parse and execute code (such as a weather API), and the **Final Response** where the model uses the tool's output to answer the user.

### Model's Turn

Here's the user prompt `"Hey, what's the weather in Tokyo right now?"`, and the tool `[get_current_weather]`. Gemma generates a function call object as follows.

    # Define a function that our model can use.
    def get_current_weather(location: str, unit: str = "celsius"):
        """
        Gets the current weather in a given location.

        Args:
            location: The city and state, e.g. "San Francisco, CA" or "Tokyo, JP"
            unit: The unit to return the temperature in. (choices: ["celsius", "fahrenheit"])

        Returns:
            temperature: The current temperature in the given location
            weather: The current weather in the given location
        """
        return {"temperature": 15, "weather": "sunny"}

    prompt = "Hey, what's the weather in Tokyo right now?"
    tools = [get_current_weather]

    message = [
        {
            "role": "system", "content": "You are a helpful assistant."
        },
        {
            "role": "user", "content": prompt
        },
    ]

    text = processor.apply_chat_template(message, tools=tools, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=text, return_tensors="pt").to(model.device)
    out = model.generate(**inputs, max_new_tokens=128)
    generated_tokens = out[0][len(inputs["input_ids"][0]):]
    output = processor.decode(generated_tokens, skip_special_tokens=False)

    print(f"Prompt: {prompt}")
    print(f"Tools: {tools}")
    print(f"Output: {output}")

```
Prompt: Hey, what's the weather in Tokyo right now?
Tools: [<function get_current_weather at 0x7cef824ece00>]
Output: <|tool_call>call:get_current_weather{location:<|"|>Tokyo, JP<|"|>}<tool_call|><|tool_response>
```

### Developer's Turn

Your application should parse the model's response to extract the function name and argments, and append `tool_calls` and `tool_responses` with the `assistant` role.
>
> > [!NOTE]
> > **Note:** Always validate function names and arguments before execution.
>
    import re
    import json

    def extract_tool_calls(text):
        def cast(v):
            try: return int(v)
            except:
                try: return float(v)
                except: return {'true': True, 'false': False}.get(v.lower(), v.strip("'\""))

        return [{
            "name": name,
            "arguments": {
                k: cast((v1 or v2).strip())
                for k, v1, v2 in re.findall(r'(\w+):(?:<\|"\|>(.*?)<\|"\|>|([^,}]*))', args)
            }
        } for name, args in re.findall(r"<\|tool_call>call:(\w+)\{(.*?)\}<tool_call\|>", text, re.DOTALL)]

    calls = extract_tool_calls(output)
    if calls:
        # Call the function and get the result
        #####################################
        # WARNING: This is a demonstration. #
        #####################################
        # Using globals() to call functions dynamically can be dangerous in
        # production. In a real application, you should implement a secure way to
        # map function names to actual function calls, such as a predefined
        # dictionary of allowed tools and their implementations.
        results = [
            {"name": c['name'], "response": globals()[c['name']](**c['arguments'])}
            for c in calls
        ]

        message.append({
            "role": "assistant",
            "tool_calls": [
                {"function": call} for call in calls
            ],
            "tool_responses": results
        })
        print(json.dumps(message[-1], indent=2))

```
{
  "role": "assistant",
  "tool_calls": [
    {
      "function": {
        "name": "get_current_weather",
        "arguments": {
          "location": "Tokyo, JP"
        }
      }
    }
  ],
  "tool_responses": [
    {
      "name": "get_current_weather",
      "response": {
        "temperature": 15,
        "weather": "sunny"
      }
    }
  ]
}
```
>
> > [!NOTE]
> > **Note:** For optimal results, append the tool execution result to your message history using the specific format below. This ensures the chat template correctly generates the required token structure (e.g., `response:get_current_weather{temperature:15,weather:<|"|>sunny<|"|>}`).
>
    "tool_responses": [
      {
        "name": function_name,
        "response": function_response
      }
    ]

In case of multiple independent requests:

    "tool_responses": [
      {
        "name": function_name_1,
        "response": function_response_1
      },
      {
        "name": function_name_2,
        "response": function_response_2
      }
    ]

### Final Response

Finally, Gemma reads the tool response and reply to the user.

    text = processor.apply_chat_template(message, tools=tools, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=text, return_tensors="pt").to(model.device)
    out = model.generate(**inputs, max_new_tokens=128)
    generated_tokens = out[0][len(inputs["input_ids"][0]):]
    output = processor.decode(generated_tokens, skip_special_tokens=True)
    print(f"Output: {output}")
    message[-1]["content"] = output

```
Output: The current weather in Tokyo is 15 degrees and sunny.
```

You can see the full chat history below.

    # full history
    print(json.dumps(message, indent=2))

    print("-"*80)
    output = processor.decode(out[0], skip_special_tokens=False)
    print(f"Output: {output}")

```
[
  {
    "role": "system",
    "content": "You are a helpful assistant."
  },
  {
    "role": "user",
    "content": "Hey, what's the weather in Tokyo right now?"
  },
  {
    "role": "assistant",
    "tool_calls": [
      {
        "function": {
          "name": "get_current_weather",
          "arguments": {
            "location": "Tokyo, JP"
          }
        }
      }
    ],
    "tool_responses": [
      {
        "name": "get_current_weather",
        "response": {
          "temperature": 15,
          "weather": "sunny"
        }
      }
    ],
    "content": "The current weather in Tokyo is 15 degrees and sunny."
  }
]
---
Output: <bos><|turn>system
You are a helpful assistant.<|tool>declaration:get_current_weather{description:<|"|>Gets the current weather in a given location.<|"|>,parameters:{properties:{location:{description:<|"|>The city and state, e.g. "San Francisco, CA" or "Tokyo, JP"<|"|>,type:<|"|>STRING<|"|>},unit:{description:<|"|>The unit to return the temperature in.<|"|>,enum:[<|"|>celsius<|"|>,<|"|>fahrenheit<|"|>],type:<|"|>STRING<|"|>} },required:[<|"|>location<|"|>],type:<|"|>OBJECT<|"|>} }<tool|><turn|>
<|turn>user
Hey, what's the weather in Tokyo right now?<turn|>
<|turn>model
<|tool_call>call:get_current_weather{location:<|"|>Tokyo, JP<|"|>}<tool_call|><|tool_response>response:get_current_weather{temperature:15,weather:<|"|>sunny<|"|>}<tool_response|>The current weather in Tokyo is 15 degrees and sunny.<turn|>
```

### Function calling with Thinking

By utilizing an internal reasoning process, the model significantly enhances its function-calling accuracy. This allows for more precise decision-making regarding when to trigger a tool and how to define its parameters.

    prompt = "Hey, I'm in Seoul. Is it good for running now?"
    message = [
        {
            "role": "system", "content": "You are a helpful assistant."
        },
        {
            "role": "user", "content": prompt
        },
    ]

    text = processor.apply_chat_template(message, tools=tools, tokenize=False, add_generation_prompt=True, enable_thinking=True)
    inputs = processor(text=text, return_tensors="pt").to(model.device)
    input_len = inputs["input_ids"].shape[-1]

    out = model.generate(**inputs, max_new_tokens=1024)
    output = processor.decode(out[0][input_len:], skip_special_tokens=False)
    result = processor.parse_response(output)

    for key, value in result.items():
      if key == "role":
        print(f"Role: {value}")
      elif key == "thinking":
        print(f"\n=== Thoughts ===\n{value}")
      elif key == "content":
        print(f"\n=== Answer ===\n{value}")
      elif key == "tool_calls":
        print(f"\n=== Tool Calls ===\n{value}")
      else:
        print(f"\n{key}: {value}...\n")

````
Role: assistant

=== Thoughts ===

1.  **Analyze the Request:** The user is asking if it's "good for running now" in "Seoul".

2.  **Identify Necessary Information:** To determine if it's good for running, I need current weather information (temperature, precipitation, etc.) for Seoul.

3.  **Examine Available Tools:** The available tool is `get_current_weather(location, unit)`.

4.  **Determine Tool Arguments:**
    *   `location`: The user specified "Seoul".
    *   `unit`: The user did not specify a unit (Celsius or Fahrenheit).

5.  **Formulate the Tool Call:** I need to call `get_current_weather` with the location. Since the user didn't specify a unit, I can either omit it (if the tool defaults are acceptable) or choose a common one. However, the tool definition requires `location` but `unit` is optional.

6.  **Construct the Response Strategy:**
    *   Call the tool to get the weather data for Seoul.
    *   Once the data is received, I can advise the user on whether it's suitable for running.

7.  **Generate Tool Call:**

    ```json
    {
      "toolSpec": {
        "name": "get_current_weather",
        "args": {
          "location": "Seoul"
        }
      }
    }
    ```
    (Self-correction: The `unit` parameter is optional in the definition, so just providing the location is sufficient to proceed.)

8.  **Final Output Generation:** Present the tool call to the user/system.

=== Tool Calls ===
[{'type': 'function', 'function': {'name': 'get_current_weather', 'arguments': {'location': 'Seoul'} } }]
````

Process the tool call and get the final answer.

    calls = extract_tool_calls(output)
    if calls:
        # Call the function and get the result
        #####################################
        # WARNING: This is a demonstration. #
        #####################################
        # Using globals() to call functions dynamically can be dangerous in
        # production. In a real application, you should implement a secure way to
        # map function names to actual function calls, such as a predefined
        # dictionary of allowed tools and their implementations.
        results = [
            {"name": c['name'], "response": globals()[c['name']](**c['arguments'])}
            for c in calls
        ]

        message.append({
            "role": "assistant",
            "tool_calls": [
                {"function": call} for call in calls
            ],
            "tool_responses": results
        })

    text = processor.apply_chat_template(message, tools=tools, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=text, return_tensors="pt").to(model.device)
    out = model.generate(**inputs, max_new_tokens=128)
    generated_tokens = out[0][len(inputs["input_ids"][0]):]
    output = processor.decode(generated_tokens, skip_special_tokens=True)
    print(f"Output: {output}")
    message[-1]["content"] = output

    print("-"*80)
    print("Full History")
    print("-"*80)
    print(json.dumps(message, indent=2))

```
Output: The current weather in Seoul is 15 degrees Celsius and sunny. That sounds like great weather for a run!
---
Full History
---
[
  {
    "role": "system",
    "content": "You are a helpful assistant."
  },
  {
    "role": "user",
    "content": "Hey, I'm in Seoul. Is it good for running now?"
  },
  {
    "role": "assistant",
    "tool_calls": [
      {
        "function": {
          "name": "get_current_weather",
          "arguments": {
            "location": "Seoul"
          }
        }
      }
    ],
    "tool_responses": [
      {
        "name": "get_current_weather",
        "response": {
          "temperature": 15,
          "weather": "sunny"
        }
      }
    ],
    "content": "The current weather in Seoul is 15 degrees Celsius and sunny. That sounds like great weather for a run!"
  }
]
```

## Important Caveat: Automatic vs. Manual Schemas

When relying on automatic conversion from Python functions to JSON schema, the generated output may not always meet specific expectations regarding complex parameters.

If a function uses a custom object (like a Config class) as an argument, the automatic converter may describe it simply as a generic "object" without detailing its internal properties.

In these cases, manually defining the JSON schema is preferred to ensure nested properties (such as theme or font_size within a config object) are explicitly defined for the model.

    import json
    from transformers.utils import get_json_schema

    class Config:
        def __init__(self):
            self.theme = "light"
            self.font_size = 14

    def update_config(config: Config):
        """
        Updates the configuration of the system.

        Args:
            config: A Config object

        Returns:
            True if the configuration was successfully updated, False otherwise.
        """

    update_config_schema = {
        "type": "function",
        "function": {
            "name": "update_config",
            "description": "Updates the configuration of the system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "description": "A Config object",
                        "properties": {"theme": {"type": "string"}, "font_size": {"type": "number"} },
                        },
                    },
                "required": ["config"],
                },
            },
        }

    print(f"--- [Automatic] ---")
    print(json.dumps(get_json_schema(update_config), indent=2))

    print(f"\n--- [Manual Schemas] ---")
    print(json.dumps(update_config_schema, indent=2))

```
--- [Automatic] ---
{
  "type": "function",
  "function": {
    "name": "update_config",
    "description": "Updates the configuration of the system.",
    "parameters": {
      "type": "object",
      "properties": {
        "config": {
          "type": "object",
          "description": "A Config object"
        }
      },
      "required": [
        "config"
      ]
    }
  }
}

--- [Manual Schemas] ---
{
  "type": "function",
  "function": {
    "name": "update_config",
    "description": "Updates the configuration of the system.",
    "parameters": {
      "type": "object",
      "properties": {
        "config": {
          "type": "object",
          "description": "A Config object",
          "properties": {
            "theme": {
              "type": "string"
            },
            "font_size": {
              "type": "number"
            }
          }
        }
      },
      "required": [
        "config"
      ]
    }
  }
}
```

## Summary and next steps

You have established how to build an application that can call functions with Gemma 4. The workflow is established through a four-stage cycle:

1. **Define Tools**: Create the functions your model can use, specifying arguments and descriptions (e.g., a weather lookup function).
2. **Model's Turn**: The model receives the user's prompt and a list of available tools, returning a structured function call object instead of plain text.
3. **Developer's Turn**: The developer parses this output using regular expressions to extract function names and arguments, executes the actual Python code, and appends the results to the chat history using the specific tool role.
4. **Final Response**: The model processes the tool's execution result to generate a final, natural language answer for the user.

Check out the following documentation for further reading.

- [Run Gemma overview](https://ai.google.dev/gemma/docs/run)
- [Vision understanding](https://ai.google.dev/gemma/docs/capabilities/vision)
- [Audio understanding](https://ai.google.dev/gemma/docs/capabilities/audio)
- [Thinking mode](https://ai.google.dev/gemma/docs/capabilities/thinking)