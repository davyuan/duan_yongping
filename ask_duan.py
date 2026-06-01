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

def generate_rag_sys_prompt(reranked_results):
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
    context_str = "\n".join(context_blocks)
    
    # The System Prompt: Instructions for the LLM
    prompt = f"""
You are an investing guru. You answer questions about investing strategies and financial decisions. 
### Guidelines:
1. **You are a value investor, not a trader:** You focus on long-term investments based on fundamental analysis, not short-term market movements.
2. **Stay Grounded:** Only answer based on the provided data. If the answer isn't there, say you don't know.
3. **Formatting:** Always use first person perspective, with provided data as internalized knowledge. Use the same tone and style as in the provided answers. If the answer is formal, be formal. If the answer is casual, be casual.
--------------------
The data:

{context_str}

"""
    return prompt


args = parse_args()
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = get_collection(chroma_client, args)

#user_query = "什么时候是最佳的卖出时机？"
#user_query = "什么是价值投资？"
#user_query = "想学习价值投资，段总推荐看什么书？"
#user_query = "特斯拉是个好的价值投资标的吗？"
#user_query = "您当年为什么投资网易？"
#user_query = "在网易已经翻了20倍的时候，您为啥能做到坚持持有？"
#user_query = "茅台的目标消费人群在萎缩，白酒在中国的销量每年都在下降，茅台还是个好生意吗？"
#user_query = "为什么白酒下滑但茅台未必下滑？"
#user_query = "您看财务报表吗？怎么从财务报表里看出一个好生意，或者坏生意？"
#user_query = "如果财务报表只能用来剔除坏生意，怎么在几千家上市公司里找到好生意呢？"
user_query = input("请输入您的问题: ")#"不是每个投资者都有资源去实地调查，或者跟管理层交流，怎么通过公开信息判读一个企业的文化，或者护城河呢？"

text_results = collection.query(
    query_texts=[user_query],
    n_results=5,
)

entries = normalize_results(text_results)
reranked_results = rerank_results(entries)

client = OpenAI()

system_prompt = generate_rag_sys_prompt(reranked_results)
#print(system_prompt)

response = client.chat.completions.create(
    model="gpt-5.4-mini",
    messages = [
        {"role":"system","content":system_prompt},
        {"role":"user","content":user_query}    
    ]
)

print("\n\n---------------------\n\n")

print(response.choices[0].message.content)