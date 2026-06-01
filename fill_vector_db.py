import argparse
import json
import os

import chromadb
import dotenv
from chromadb.utils import embedding_functions


DEFAULT_JSON_FILE = "data/段永平投资问答录(投资逻辑篇).json"
DEFAULT_CHROMA_PATH = "chroma_db"
DEFAULT_COLLECTION_NAME = "duan_yongping"
DEFAULT_SANITY_TEXT_LIMIT = 6000
DEFAFULT_EMBEDDING_MODEL = "BAAI/bge-base-zh-v1.5"

dotenv.load_dotenv()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Load JSON records into Chroma using id/text/metadata fields directly."
    )
    parser.add_argument(
        "--json-file",
        default=DEFAULT_JSON_FILE,
        help="Path to the JSON file containing records with id/text/metadata fields.",
    )
    parser.add_argument(
        "--chroma-path",
        default=DEFAULT_CHROMA_PATH,
        help="Path to Chroma persistent storage.",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help="Chroma collection name.",
    )
    parser.add_argument(
        "--sanity-text-limit",
        type=int,
        default=DEFAULT_SANITY_TEXT_LIMIT,
        help="Warn when a record text length exceeds this many characters.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Upsert batch size.",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAFULT_EMBEDDING_MODEL,
        help="Embedding model name (used by local backend).",
    )
    return parser.parse_args()


def load_records(json_path):
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"JSON file not found: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        raise ValueError("Input JSON must be a list of objects.")

    return records


def build_upsert_payload(records):
    documents_to_upsert = []
    metadatas_to_upsert = []
    ids_to_upsert = []

    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Record at index {idx} is not a JSON object.")

        if "id" not in record:
            raise ValueError(f"Record at index {idx} is missing required field: id")
        if "text" not in record:
            raise ValueError(f"Record at index {idx} is missing required field: text")
        if "metadata" not in record:
            raise ValueError(f"Record at index {idx} is missing required field: metadata")

        record_id = str(record["id"])
        record_text = record["text"]
        record_metadata = record["metadata"]

        if not isinstance(record_text, str):
            raise ValueError(f"Record '{record_id}' has non-string text field.")
        if not isinstance(record_metadata, dict):
            raise ValueError(f"Record '{record_id}' has non-object metadata field.")

        ids_to_upsert.append(record_id)
        documents_to_upsert.append(record_text)
        metadatas_to_upsert.append(record_metadata)

    return documents_to_upsert, metadatas_to_upsert, ids_to_upsert


def report_text_size_sanity(documents, sanity_limit):
    if not documents:
        print("No documents found in input JSON.")
        return

    lengths = [len(doc) for doc in documents]
    max_length = max(lengths)
    min_length = min(lengths)
    avg_length = sum(lengths) / len(lengths)
    too_large_count = sum(1 for length in lengths if length > sanity_limit)

    print(
        "Text size sanity check: "
        f"count={len(lengths)}, min={min_length}, avg={avg_length:.1f}, max={max_length}, "
        f"over_limit({sanity_limit})={too_large_count}"
    )

    if too_large_count > 0:
        print(
            f"Warning: {too_large_count} record(s) exceed {sanity_limit} chars. "
            "Proceeding without chunking as requested."
        )


def upsert_in_batches(collection, documents, metadatas, ids, batch_size):
    total = len(ids)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        collection.upsert(
            documents=documents[start:end],
            metadatas=metadatas[start:end],
            ids=ids[start:end],
        )


def create_collection(chroma_client, args):
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=args.embedding_model
    )
    return chroma_client.get_or_create_collection(
        name=args.collection,
        embedding_function=embedding_fn,
    )

    # Default backend avoids external HF endpoint dependency.
    return chroma_client.get_or_create_collection(name=args.collection)


def main():
    args = parse_args()
    records = load_records(args.json_file)

    documents_to_upsert, metadatas_to_upsert, ids_to_upsert = build_upsert_payload(records)
    report_text_size_sanity(documents_to_upsert, args.sanity_text_limit)

    chroma_client = chromadb.PersistentClient(path=args.chroma_path)

    # Reset the target collection so each run starts from a clean state.
    try:
        chroma_client.delete_collection(name=args.collection)
        print(f"Deleted existing collection '{args.collection}'.")
    except Exception:
        # If it does not exist yet, continue and create it.
        pass

    collection = create_collection(chroma_client, args)

    upsert_in_batches(
        collection,
        documents_to_upsert,
        metadatas_to_upsert,
        ids_to_upsert,
        args.batch_size,
    )

    print(
        f"Finished! Upserted {len(ids_to_upsert)} records from '{args.json_file}' "
        f"into collection '{args.collection}'."
    )


if __name__ == "__main__":
    main()