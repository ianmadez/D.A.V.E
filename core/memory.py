import os
import hashlib
import re
import pickle

# simple cache for knowledge files: path -> (hash, text)
__kb_cache = {}

CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', '.dave_cache')
KNOWLEDGE_CACHE_FILE = os.path.join(CACHE_DIR, 'knowledge_skeleton.pkl')

def compress_knowledge(text: str, filename: str) -> str:
    """Compress knowledge text into micro-syntax like skeleton."""
    lines = text.splitlines()
    compressed = [filename]
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if line.startswith('#') or line.startswith('##') or re.match(r'^\d+\.', line) or 'best practice' in line.lower():
            prefix = 'H' if line.startswith('#') else 'P'
            compressed.append(f"[{prefix}:{i}]{line[:50]}")
    return '|'.join(compressed[:20])  # Limit to top 20 items

def load_compressed_knowledge():
    """Load or build compressed knowledge cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(KNOWLEDGE_CACHE_FILE):
        with open(KNOWLEDGE_CACHE_FILE, 'rb') as f:
            return pickle.load(f)
    
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'knowledge_base'))
    compressed = {}
    
    # Compress project context
    context_path = os.path.join(base_dir, 'project_context.txt')
    ctx = _read_if_changed(context_path)
    if ctx:
        compressed['project_context'] = compress_knowledge(ctx, 'project_context.txt')
    
    # Compress language files
    lang_dir = os.path.join(base_dir, 'languages')
    for fname in os.listdir(lang_dir):
        if fname.endswith('.md'):
            path = os.path.join(lang_dir, fname)
            text = _read_if_changed(path)
            if text:
                compressed[fname] = compress_knowledge(text, fname)
    
    with open(KNOWLEDGE_CACHE_FILE, 'wb') as f:
        pickle.dump(compressed, f)
    return compressed

def _read_if_changed(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except Exception:
        return None
    h = hashlib.sha256(data).hexdigest()
    cached = __kb_cache.get(path)
    if cached and cached[0] == h:
        return cached[1]
    text = data.decode('utf-8', errors='ignore')
    __kb_cache[path] = (h, text)
    return text


def load_knowledge_base(current_file=None, compressed=True, prompt=None, max_chars=2000, top_k=1):
    """
    Optimized Hybrid Memory Router.
    Injects only the project context and the top ranked language rules matching the task profile.
    """
    # Fallback to full text
    if not compressed:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'knowledge_base'))
        knowledge = ""
        
        # 1. Load General Project Context
        context_path = os.path.join(base_dir, 'project_context.txt')
        ctx = _read_if_changed(context_path)
        if ctx is not None:
            knowledge += f"--- GLOBAL RULES ---\n{ctx.strip()}\n\n"

        # 2. Language Sniffing (The Brain Boost)
        if current_file:
            ext = os.path.splitext(current_file)[1].lower()
            lang_map = {
                '.py': 'python.md',
                '.html': 'html.md',
                '.js': 'javascript.md',
                '.jsx': 'javascript.md',
                '.ts': 'javascript.md',
                '.tsx': 'javascript.md',
                '.css': 'css.md'
            }
            if ext in lang_map:
                lang_file = os.path.join(base_dir, 'languages', lang_map[ext])
                lang_text = _read_if_changed(lang_file)
                if lang_text is not None:
                    knowledge += f"--- {ext.upper()} SENIOR DEV MANUAL ---\n{lang_text.strip()}\n\n"
        
        return knowledge.strip()[:max_chars]

    # Execute Token-Minified Extraction Route
    try:
        compressed_kb = load_compressed_knowledge()  # Returns dict: filename -> text contents
    except Exception:
        return ""

    project_ctx = compressed_kb.get('project_context', '')
    
    # If there is no explicit instruction trail, exit early with base context parameters
    if not prompt and not current_file:
        return project_ctx[:max_chars]

    candidates = []
    extension_map = {
        '.py': 'python.md', 
        '.js': 'javascript.md', 
        '.jsx': 'javascript.md',
        '.ts': 'javascript.md',
        '.tsx': 'javascript.md',
        '.html': 'html.md', 
        '.css': 'css.md'
    }

    # Strategy 1: Extension Mapping (Instant structural pin)
    if current_file:
        ext = os.path.splitext(current_file)[1].lower()
        target_doc = extension_map.get(ext)
        if target_doc and target_doc in compressed_kb:
            candidates.append(compressed_kb[target_doc])

    # Strategy 2: Prompt Overlap Ranking (Find secondary relevant files)
    if prompt:
        query_tokens = set(re.findall(r'\w+', prompt.lower()))
        scored_docs = []
        
        for doc_name, doc_content in compressed_kb.items():
            # Skip non-instruction elements or what was already pulled via extension mapping
            if doc_name in ['project_context', 'global_rules.md'] or (current_file and extension_map.get(os.path.splitext(current_file)[1].lower()) == doc_name):
                continue
                
            # Score documentation based on phrase token intersections
            content_tokens = set(re.findall(r'\w+', doc_content.lower()))
            overlap_score = len(query_tokens.intersection(content_tokens))
            if overlap_score > 0:
                scored_docs.append((overlap_score, doc_content))
        
        # Sort by relevance and extract the top asset matching our limit parameters
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        for _, doc_text in scored_docs[:top_k]:
            candidates.append(doc_text)

    # Consolidate strings under a rigid architectural safety budget
    combined_knowledge = project_ctx
    if candidates:
        combined_knowledge += "\n\n=== RELEVANT CONVENTIONS ===\n" + "\n---\n".join(candidates)

    return combined_knowledge[:max_chars]