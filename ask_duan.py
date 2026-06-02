import chromadb
import os
import argparse
from openai import OpenAI
from dotenv import load_dotenv
from chromadb.utils import embedding_functions

load_dotenv()

# setting the environment

DATA_PATH = r"data"
CHROMA_PATH = r"chroma_db"
COLLECTION_NAME = "duan_yongping"
EMBEDDING_MODEL = "BAAI/bge-base-zh-v1.5"


def parse_args():
    parser = argparse.ArgumentParser(description="Query Chroma collection for investing Q&A.")

    parser.add_argument(
        "--embedding-model",
        default=EMBEDDING_MODEL,
        help="Embedding model name for huggingface_api mode.",
    )
    return parser.parse_args()

def get_collection(chroma_client, args):
    if args.embedding_model == "BAAI/bge-base-zh-v1.5":
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=args.embedding_model,
        )
        return chroma_client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=embedding_fn,
        )

    # Auto mode: rely on whatever embedding function is already persisted.
    return chroma_client.get_collection(name=COLLECTION_NAME)

def normalize_results(results):
    """Flatten a single Chroma query response to the shared entry format."""
    entries = []
    for i in range(len(results["ids"][0])):
        entries.append({
            "id": results["ids"][0][i],
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i] if "distances" in results else None,
        })
    return entries


def rerank_results(entries):
    """Sort the provided entries to surface tables first and use distance as a tiebreaker."""
    return sorted(
        entries,
        key=lambda x: (
            x["metadata"].get("is_table", False),
            -x["distance"] if x["distance"] is not None else 0,
        ),
        reverse=True,
    )

def format_retrieved_context(reranked_results):
    context_blocks = []

    for res in reranked_results:
        metadata = res["metadata"]
        content = res["document"]
        source = metadata.get("page", "Unknown")
        header = metadata.get("chapter", metadata.get("sector", "General Info"))

        # Format text blocks
        block = f"--- TEXT FROM Page {source} ({header}) ---\n{content}\n"

        context_blocks.append(block)

    # Combine all blocks into a single context string
    return "\n".join(context_blocks)


def generate_base_system_prompt():
    return """
You are an investing guru. You answer questions about investing strategies and financial decisions. 
### Guidelines:
1. **You are a value investor, not a trader:** You focus on long-term investments based on fundamental analysis, not short-term market movements.
2. **Stay Grounded:** Only answer based on the provided data. If the answer isn't there, say you don't know.
3. **Formatting:** Always use first person perspective, with provided data as internalized knowledge. Use the same tone and style as in the provided answers. If the answer is formal, be formal. If the answer is casual, be casual.
"""


def build_augmented_user_message(user_query, context_str):
    return (
        f"用户问题:\n{user_query}\n\n"
        "以下是检索到的参考资料，请优先依据这些资料回答；如果资料不足，请明确说明不知道:\n"
        f"{context_str}"
    )


args = parse_args()
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = get_collection(chroma_client, args)
client = OpenAI()

chat_history = []
system_prompt = generate_base_system_prompt()

print("输入问题开始对话，按 Ctrl+C 结束。")

try:
    while True:
        user_query = input("\n请输入您的问题: ").strip()
        if not user_query:
            print("请输入非空问题。")
            continue

        text_results = collection.query(
            query_texts=[user_query],
            n_results=5,
        )

        entries = normalize_results(text_results)
        reranked_results = rerank_results(entries)
        context_str = format_retrieved_context(reranked_results)
        current_user_message = build_augmented_user_message(user_query, context_str)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(chat_history)
        messages.append({"role": "user", "content": current_user_message})

        response = client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=messages,
        )

        assistant_content = response.choices[0].message.content

        # Save only clean dialogue history (without retrieval context) for future turns.
        chat_history.append({"role": "user", "content": user_query})
        chat_history.append({"role": "assistant", "content": assistant_content})

        print("\n\n---------------------\n\n")
        print(assistant_content)
except KeyboardInterrupt:
    print("\n\n会话结束。")