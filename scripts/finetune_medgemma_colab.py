#!/usr/bin/env python3
"""QLoRA fine-tuning for MedGemma in Google Colab.

The script expects a Hugging Face or local ``DatasetDict`` with
train/validation splits and these columns:

``image``
    A ``datasets.Image`` value or a path that ``datasets.Image`` can decode.
``prompt``
    The clinician-facing instruction/question.
``response``
    The reviewed target response. For ``--task-mode report`` this should be a
    JSON object or a JSON-encoded string matching ``REPORT_KEYS``.

If no validation split exists, ``--group-column`` is required so the script can
make a deterministic group-level split without leaking one patient/study across
train and validation. The script never uploads the dataset or evaluation rows.
W&B receives aggregate metrics by default; raw prompts/references/predictions
are logged only with ``--log-eval-samples-to-wandb``.

This is research/demo code, not a medical device. Generated output must remain
clinician-reviewed, non-diagnostic decision support.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


REPORT_KEYS = ("summary", "findings", "impression", "recommendation", "confidence")
DEFAULT_SYSTEM_PROMPT = (
    "You are a radiology decision-support assistant. Produce a draft for clinician review only. "
    "Do not claim diagnostic certainty or replace clinical judgment. Return only valid JSON with "
    "the keys summary, findings, impression, recommendation, and confidence. Confidence must be "
    "a number from 0 to 1."
)


@dataclass(frozen=True)
class RunConfig:
    model_id: str
    dataset_id: str | None
    dataset_path: str | None
    dataset_config: str | None
    train_split: str
    eval_split: str
    image_column: str
    prompt_column: str
    response_column: str
    group_column: str | None
    eval_fraction: float
    task_mode: str
    system_prompt: str
    output_dir: str
    hub_model_id: str | None
    hub_private: bool
    push_to_hub: bool
    wandb_project: str
    wandb_entity: str | None
    run_name: str
    log_eval_samples_to_wandb: bool
    max_train_samples: int | None
    max_eval_samples: int | None
    generation_eval_samples: int
    seed: int
    epochs: float
    learning_rate: float
    train_batch_size: int
    eval_batch_size: int
    gradient_accumulation_steps: int
    logging_steps: int
    eval_steps: int
    save_steps: int
    lora_rank: int
    lora_alpha: int
    lora_dropout: float
    max_new_tokens: int
    min_rouge_l_delta: float
    min_json_valid_rate: float


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default="google/medgemma-4b-it")
    dataset_source = parser.add_mutually_exclusive_group(required=True)
    dataset_source.add_argument("--dataset-id", help="Hugging Face dataset repository ID")
    dataset_source.add_argument(
        "--dataset-path",
        help="Local DatasetDict saved with save_to_disk(), or a JSON/JSONL file (for example on Drive)",
    )
    parser.add_argument("--dataset-config")
    parser.add_argument("--train-split", default="train")
    parser.add_argument("--eval-split", default="validation")
    parser.add_argument("--image-column", default="image")
    parser.add_argument("--prompt-column", default="prompt")
    parser.add_argument("--response-column", default="response")
    parser.add_argument("--group-column", default="patient_id")
    parser.add_argument("--eval-fraction", type=float, default=0.1)
    parser.add_argument("--task-mode", choices=("report", "classification", "free_text"), default="report")
    parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--output-dir", default="/content/medtrace-medgemma-output")
    parser.add_argument("--hub-model-id")
    parser.add_argument("--hub-private", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--push-to-hub", action="store_true")
    parser.add_argument("--wandb-project", default="medtrace-medgemma")
    parser.add_argument("--wandb-entity")
    parser.add_argument("--run-name", default="medgemma-4b-medtrace-qlora")
    parser.add_argument("--log-eval-samples-to-wandb", action="store_true")
    parser.add_argument("--max-train-samples", type=int)
    parser.add_argument("--max-eval-samples", type=int)
    parser.add_argument("--generation-eval-samples", type=int, default=32)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--train-batch-size", type=int, default=2)
    parser.add_argument("--eval-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--eval-steps", type=int, default=50)
    parser.add_argument("--save-steps", type=int, default=50)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--min-rouge-l-delta", type=float, default=0.0)
    parser.add_argument("--min-json-valid-rate", type=float, default=0.95)
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> RunConfig:
    if not 0 < args.eval_fraction < 1:
        raise ValueError("--eval-fraction must be between 0 and 1")
    if args.generation_eval_samples < 1:
        raise ValueError("--generation-eval-samples must be positive")
    if args.push_to_hub and not args.hub_model_id:
        raise ValueError("--hub-model-id is required with --push-to-hub")
    group_column = args.group_column.strip() if args.group_column else None
    return RunConfig(
        model_id=args.model_id,
        dataset_id=args.dataset_id,
        dataset_path=args.dataset_path,
        dataset_config=args.dataset_config,
        train_split=args.train_split,
        eval_split=args.eval_split,
        image_column=args.image_column,
        prompt_column=args.prompt_column,
        response_column=args.response_column,
        group_column=group_column,
        eval_fraction=args.eval_fraction,
        task_mode=args.task_mode,
        system_prompt=args.system_prompt,
        output_dir=args.output_dir,
        hub_model_id=args.hub_model_id,
        hub_private=args.hub_private,
        push_to_hub=args.push_to_hub,
        wandb_project=args.wandb_project,
        wandb_entity=args.wandb_entity,
        run_name=args.run_name,
        log_eval_samples_to_wandb=args.log_eval_samples_to_wandb,
        max_train_samples=args.max_train_samples,
        max_eval_samples=args.max_eval_samples,
        generation_eval_samples=args.generation_eval_samples,
        seed=args.seed,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        train_batch_size=args.train_batch_size,
        eval_batch_size=args.eval_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        logging_steps=args.logging_steps,
        eval_steps=args.eval_steps,
        save_steps=args.save_steps,
        lora_rank=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        max_new_tokens=args.max_new_tokens,
        min_rouge_l_delta=args.min_rouge_l_delta,
        min_json_valid_rate=args.min_json_valid_rate,
    )


def normalize_response(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def stable_eval_membership(value: Any, *, fraction: float, seed: int) -> bool:
    digest = hashlib.sha256(f"{seed}:{value}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(2**64)
    return bucket < fraction


def parse_json_object(text: str) -> dict[str, Any] | None:
    candidates = [text.strip()]
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def report_schema_valid(text: str) -> bool:
    parsed = parse_json_object(text)
    if parsed is None or not all(key in parsed for key in REPORT_KEYS):
        return False
    confidence = parsed.get("confidence")
    return isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= confidence <= 1


def normalized_exact_match(reference: str, prediction: str) -> bool:
    normalize = lambda value: " ".join(value.casefold().split())
    return normalize(reference) == normalize(prediction)


def package_versions(names: Iterable[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def validate_dataset_columns(dataset: Any, config: RunConfig) -> None:
    required = {config.image_column, config.prompt_column, config.response_column}
    for split_name, split in dataset.items():
        missing = sorted(required - set(split.column_names))
        if missing:
            raise ValueError(f"Dataset split {split_name!r} is missing columns: {missing}")
        if len(split) == 0:
            raise ValueError(f"Dataset split {split_name!r} is empty")


def load_dataset_splits(config: RunConfig) -> tuple[Any, Any, dict[str, Any]]:
    from datasets import DatasetDict, Image, load_dataset, load_from_disk

    if config.dataset_id:
        dataset = load_dataset(config.dataset_id, config.dataset_config, token=os.environ.get("HF_TOKEN"))
        dataset_source = f"hub:{config.dataset_id}"
    else:
        dataset_path = Path(config.dataset_path or "").expanduser().resolve()
        if not dataset_path.exists():
            raise FileNotFoundError(f"Dataset path does not exist: {dataset_path}")
        if dataset_path.is_dir():
            dataset = load_from_disk(str(dataset_path))
        elif dataset_path.suffix.lower() in {".json", ".jsonl"}:
            dataset = load_dataset("json", data_files={config.train_split: str(dataset_path)})
        else:
            raise ValueError("--dataset-path must be a save_to_disk() directory or a .json/.jsonl file")
        # Avoid recording a private Colab/Drive path in W&B or model metadata.
        dataset_source = f"local:{dataset_path.name}"
    if not isinstance(dataset, DatasetDict):
        raise TypeError("Expected load_dataset() to return a DatasetDict")
    if config.train_split not in dataset:
        raise ValueError(f"Missing training split {config.train_split!r}; available: {list(dataset)}")

    if config.eval_split in dataset:
        train = dataset[config.train_split]
        evaluation = dataset[config.eval_split]
        split_strategy = f"existing:{config.train_split}/{config.eval_split}"
    else:
        source = dataset[config.train_split]
        if not config.group_column or config.group_column not in source.column_names:
            raise ValueError(
                f"No {config.eval_split!r} split exists. Provide a patient/study group column with "
                "--group-column so evaluation can be separated without leakage."
            )
        flags = [
            stable_eval_membership(value, fraction=config.eval_fraction, seed=config.seed)
            for value in source[config.group_column]
        ]
        train_indices = [index for index, flag in enumerate(flags) if not flag]
        eval_indices = [index for index, flag in enumerate(flags) if flag]
        if not train_indices or not eval_indices:
            raise ValueError(
                "Deterministic group split produced an empty train or validation set; increase the data "
                "or adjust --eval-fraction/--seed."
            )
        train = source.select(train_indices)
        evaluation = source.select(eval_indices)
        split_strategy = f"group-hash:{config.group_column}"

    working = DatasetDict({"train": train, "validation": evaluation})
    validate_dataset_columns(working, config)

    train_groups: set[str] = set()
    eval_groups: set[str] = set()
    if config.group_column:
        group_presence = {
            split_name: config.group_column in working[split_name].column_names
            for split_name in ("train", "validation")
        }
        if len(set(group_presence.values())) > 1:
            raise ValueError(
                f"Group column {config.group_column!r} must exist in both train and validation or neither"
            )
        if all(group_presence.values()):
            train_groups = {str(value) for value in working["train"][config.group_column]}
            eval_groups = {str(value) for value in working["validation"][config.group_column]}
            overlap = train_groups & eval_groups
            if overlap:
                raise ValueError(f"Patient/study leakage detected across splits ({len(overlap)} overlapping groups)")

    for split_name in tuple(working):
        split = working[split_name]
        if config.image_column != "image":
            split = split.rename_column(config.image_column, "image")
        if split.features["image"].__class__.__name__ != "Image":
            split = split.cast_column("image", Image(decode=True))
        working[split_name] = split

    if config.max_train_samples is not None:
        sample_count = min(config.max_train_samples, len(working["train"]))
        working["train"] = working["train"].shuffle(seed=config.seed).select(range(sample_count))
    if config.max_eval_samples is not None:
        sample_count = min(config.max_eval_samples, len(working["validation"]))
        working["validation"] = working["validation"].shuffle(seed=config.seed).select(range(sample_count))

    def format_example(example: dict[str, Any]) -> dict[str, Any]:
        prompt = str(example[config.prompt_column]).strip()
        response = normalize_response(example[config.response_column])
        if not prompt or not response:
            raise ValueError("Dataset rows must contain non-empty prompt and response values")
        if config.task_mode == "report" and not report_schema_valid(response):
            raise ValueError(
                "Report responses must be valid JSON with summary, findings, impression, recommendation, "
                "and confidence between 0 and 1"
            )
        return {
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": config.system_prompt}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "image"},
                        {"type": "text", "text": prompt},
                    ],
                },
                {"role": "assistant", "content": [{"type": "text", "text": response}]},
            ],
            "reference": response,
            "prompt": prompt,
        }

    working = working.map(format_example, desc="Formatting MedGemma conversations")

    manifest = {
        "dataset_source": dataset_source,
        "dataset_config": config.dataset_config,
        "split_strategy": split_strategy,
        "train_rows": len(working["train"]),
        "validation_rows": len(working["validation"]),
        "train_groups": len(train_groups) if train_groups else None,
        "validation_groups": len(eval_groups) if eval_groups else None,
        "columns": working["train"].column_names,
    }
    return working["train"], working["validation"], manifest


def make_collator(processor: Any) -> Any:
    import torch

    processor.tokenizer.padding_side = "right"

    def collate(examples: list[dict[str, Any]]) -> dict[str, Any]:
        images = [[example["image"].convert("RGB")] for example in examples]
        full_texts = [
            processor.apply_chat_template(example["messages"], add_generation_prompt=False, tokenize=False).strip()
            for example in examples
        ]
        prompt_texts = [
            processor.apply_chat_template(example["messages"][:-1], add_generation_prompt=True, tokenize=False).strip()
            for example in examples
        ]
        batch = processor(text=full_texts, images=images, return_tensors="pt", padding=True)
        labels = batch["input_ids"].clone()

        labels[labels == processor.tokenizer.pad_token_id] = -100
        special_ids: set[int] = set()
        for key in ("boi_token", "eoi_token", "image_token"):
            token = processor.tokenizer.special_tokens_map.get(key)
            if isinstance(token, str):
                token_id = processor.tokenizer.convert_tokens_to_ids(token)
                if isinstance(token_id, int) and token_id >= 0:
                    special_ids.add(token_id)
        image_token_id = getattr(processor, "image_token_id", None)
        if isinstance(image_token_id, int):
            special_ids.add(image_token_id)
        special_ids.add(262144)  # MedGemma/Gemma 3 soft-image token.
        for token_id in special_ids:
            labels[labels == token_id] = -100

        # Train only on the reviewed assistant completion, not the prompt.
        for row, (prompt_text, row_images) in enumerate(zip(prompt_texts, images, strict=True)):
            prompt_batch = processor(text=[prompt_text], images=[row_images], return_tensors="pt", padding=False)
            prompt_length = min(prompt_batch["input_ids"].shape[1], labels.shape[1])
            labels[row, :prompt_length] = -100

        if not torch.any(labels != -100):
            raise ValueError("A batch contains no assistant tokens to train on; check the dataset response values")
        batch["labels"] = labels
        return batch

    return collate


def generate_prediction(model: Any, processor: Any, example: dict[str, Any], max_new_tokens: int) -> str:
    import torch

    prompt_text = processor.apply_chat_template(
        example["messages"][:-1], add_generation_prompt=True, tokenize=False
    ).strip()
    inputs = processor(
        text=[prompt_text],
        images=[[example["image"].convert("RGB")]],
        return_tensors="pt",
        padding=False,
    )
    device = next(model.parameters()).device
    inputs = {key: value.to(device) if hasattr(value, "to") else value for key, value in inputs.items()}
    input_length = inputs["input_ids"].shape[1]
    with torch.inference_mode():
        generated = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            use_cache=True,
        )
    completion_ids = generated[:, input_length:]
    return processor.batch_decode(completion_ids, skip_special_tokens=True)[0].strip()


def generation_metrics(
    *,
    references: list[str],
    predictions: list[str],
    task_mode: str,
    prefix: str,
) -> dict[str, float]:
    import evaluate

    if len(references) != len(predictions) or not references:
        raise ValueError("references and predictions must be non-empty and the same length")
    rouge = evaluate.load("rouge").compute(predictions=predictions, references=references, use_stemmer=True)
    metrics = {
        f"{prefix}/rouge1": float(rouge["rouge1"]),
        f"{prefix}/rougeL": float(rouge["rougeL"]),
        f"{prefix}/exact_match": sum(
            normalized_exact_match(reference, prediction)
            for reference, prediction in zip(references, predictions, strict=True)
        )
        / len(references),
    }
    if task_mode == "report":
        metrics[f"{prefix}/json_valid_rate"] = sum(report_schema_valid(value) for value in predictions) / len(predictions)
    return metrics


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = build_config(args)
    output_dir = Path(config.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not os.environ.get("HF_TOKEN"):
        raise RuntimeError("HF_TOKEN is required for gated MedGemma access and optional Hub publishing")
    if not os.environ.get("WANDB_API_KEY"):
        raise RuntimeError("WANDB_API_KEY is required for W&B run tracking")

    import torch
    import wandb
    from huggingface_hub import HfApi, login
    from peft import LoraConfig
    from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig, set_seed
    from trl import SFTConfig, SFTTrainer

    if not torch.cuda.is_available():
        raise RuntimeError("A CUDA GPU is required; choose an A100 runtime in Colab")
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("The selected GPU must support bfloat16; choose an A100 or newer Colab GPU")

    set_seed(config.seed)
    login(token=os.environ["HF_TOKEN"], add_to_git_credential=False)
    wandb.login(key=os.environ["WANDB_API_KEY"], relogin=True)
    os.environ["WANDB_PROJECT"] = config.wandb_project
    os.environ["WANDB_LOG_MODEL"] = "false"
    os.environ["WANDB_WATCH"] = "false"

    train_dataset, eval_dataset, dataset_manifest = load_dataset_splits(config)
    versions = package_versions(
        ("accelerate", "bitsandbytes", "datasets", "evaluate", "peft", "torch", "transformers", "trl", "wandb")
    )
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "gpu": torch.cuda.get_device_name(0),
        "packages": versions,
        "config": {
            key: (Path(value).name if key == "dataset_path" and value else value)
            for key, value in asdict(config).items()
            if "token" not in key.lower()
        },
        "dataset": dataset_manifest,
        "disclaimer": "Research/demo decision support only; clinician verification required.",
    }
    write_json(output_dir / "run_manifest.json", manifest)

    run = wandb.init(
        project=config.wandb_project,
        entity=config.wandb_entity,
        name=config.run_name,
        job_type="sft",
        config=manifest,
        tags=["medgemma", "qlora", "medical-vlm", config.task_mode],
    )

    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_quant_storage=torch.bfloat16,
    )
    model = AutoModelForImageTextToText.from_pretrained(
        config.model_id,
        token=os.environ["HF_TOKEN"],
        torch_dtype=torch.bfloat16,
        device_map="auto",
        attn_implementation="eager",
        quantization_config=quantization,
    )
    processor = AutoProcessor.from_pretrained(config.model_id, token=os.environ["HF_TOKEN"])
    processor.tokenizer.padding_side = "right"
    model.config.use_cache = False

    eval_count = min(config.generation_eval_samples, len(eval_dataset))
    generation_eval = eval_dataset.select(range(eval_count))
    references = [str(value) for value in generation_eval["reference"]]
    base_predictions = [
        generate_prediction(model, processor, generation_eval[index], config.max_new_tokens)
        for index in range(eval_count)
    ]
    base_metrics = generation_metrics(
        references=references,
        predictions=base_predictions,
        task_mode=config.task_mode,
        prefix="base",
    )
    run.log(base_metrics)

    peft_config = LoraConfig(
        r=config.lora_rank,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        bias="none",
        target_modules="all-linear",
        task_type="CAUSAL_LM",
        modules_to_save=["lm_head", "embed_tokens"],
    )
    training_args = SFTConfig(
        output_dir=str(output_dir),
        num_train_epochs=config.epochs,
        per_device_train_batch_size=config.train_batch_size,
        per_device_eval_batch_size=config.eval_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        learning_rate=config.learning_rate,
        warmup_ratio=0.03,
        lr_scheduler_type="linear",
        optim="adamw_torch_fused",
        bf16=True,
        max_grad_norm=0.3,
        logging_steps=config.logging_steps,
        eval_strategy="steps",
        eval_steps=config.eval_steps,
        save_strategy="steps",
        save_steps=config.save_steps,
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        report_to=["wandb"],
        run_name=config.run_name,
        remove_unused_columns=False,
        dataset_kwargs={"skip_prepare_dataset": True},
        label_names=["labels"],
        push_to_hub=False,
    )
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=make_collator(processor),
        peft_config=peft_config,
    )
    trainer.model.print_trainable_parameters()
    train_result = trainer.train()
    trainer.save_model(str(output_dir))
    processor.save_pretrained(str(output_dir))

    trainer.model.config.use_cache = True
    tuned_predictions = [
        generate_prediction(trainer.model, processor, generation_eval[index], config.max_new_tokens)
        for index in range(eval_count)
    ]
    tuned_metrics = generation_metrics(
        references=references,
        predictions=tuned_predictions,
        task_mode=config.task_mode,
        prefix="tuned",
    )
    trainer_metrics = {key: float(value) for key, value in trainer.evaluate().items() if isinstance(value, (int, float))}
    if "eval_loss" in trainer_metrics:
        trainer_metrics["eval_perplexity"] = math.exp(min(trainer_metrics["eval_loss"], 20))

    tuned_rouge_l = tuned_metrics["tuned/rougeL"]
    base_rouge_l = base_metrics["base/rougeL"]
    comparison = {
        "eval/rougeL_delta": tuned_rouge_l - base_rouge_l,
        "eval/publish_gate_passed": 1.0,
    }
    gate_failures: list[str] = []
    if comparison["eval/rougeL_delta"] < config.min_rouge_l_delta:
        gate_failures.append(
            f"ROUGE-L delta {comparison['eval/rougeL_delta']:.4f} < {config.min_rouge_l_delta:.4f}"
        )
    if config.task_mode == "report":
        json_valid_rate = tuned_metrics["tuned/json_valid_rate"]
        if json_valid_rate < config.min_json_valid_rate:
            gate_failures.append(f"JSON valid rate {json_valid_rate:.4f} < {config.min_json_valid_rate:.4f}")
    if gate_failures:
        comparison["eval/publish_gate_passed"] = 0.0

    all_metrics = {
        **base_metrics,
        **tuned_metrics,
        **{f"trainer/{key}": value for key, value in trainer_metrics.items()},
        **comparison,
        "train/train_runtime": float(train_result.metrics.get("train_runtime", 0.0)),
    }
    run.log(all_metrics)
    write_json(output_dir / "eval_metrics.json", {**all_metrics, "gate_failures": gate_failures})

    prediction_rows = [
        {
            "index": index,
            "prompt": str(generation_eval[index]["prompt"]),
            "reference": references[index],
            "base_prediction": base_predictions[index],
            "tuned_prediction": tuned_predictions[index],
        }
        for index in range(eval_count)
    ]
    write_jsonl(output_dir / "eval_predictions.jsonl", prediction_rows)

    if config.log_eval_samples_to_wandb:
        table = wandb.Table(columns=["index", "prompt", "reference", "base_prediction", "tuned_prediction"])
        for row in prediction_rows:
            table.add_data(
                row["index"], row["prompt"], row["reference"], row["base_prediction"], row["tuned_prediction"]
            )
        run.log({"eval/samples": table})

    published = False
    if config.push_to_hub:
        if gate_failures:
            print("Skipping Hugging Face publication because evaluation gates failed:", file=sys.stderr)
            for failure in gate_failures:
                print(f"- {failure}", file=sys.stderr)
        else:
            trainer.create_model_card(
                model_name=config.hub_model_id,
                dataset_name=config.dataset_id or "private-local-dataset",
                tags=["medgemma", "medical", "vision-language-model", "peft", "lora"],
            )
            trainer.model.push_to_hub(
                config.hub_model_id,
                private=config.hub_private,
                token=os.environ["HF_TOKEN"],
                commit_message=f"Publish {config.run_name} after held-out evaluation",
            )
            processor.push_to_hub(
                config.hub_model_id,
                private=config.hub_private,
                token=os.environ["HF_TOKEN"],
                commit_message="Add MedGemma processor files",
            )
            hub_api = HfApi(token=os.environ["HF_TOKEN"])
            for filename in ("README.md", "run_manifest.json", "eval_metrics.json"):
                local_file = output_dir / filename
                if local_file.exists():
                    hub_api.upload_file(
                        path_or_fileobj=str(local_file),
                        path_in_repo=filename,
                        repo_id=config.hub_model_id,
                        repo_type="model",
                        commit_message=f"Add {filename}",
                    )
            published = True

    run.summary.update({**all_metrics, "hub/published": published, "hub/model_id": config.hub_model_id or ""})
    run.finish()
    print(json.dumps({"output_dir": str(output_dir), "published": published, "metrics": all_metrics}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
