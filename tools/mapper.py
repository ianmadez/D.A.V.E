import os
import re
import ast
import json
import pickle
import time
from rank_bm25 import BM25Okapi

# Folders to ignore during indexing
IGNORE_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.dave_cache', 'dist', 'build', '.next'}
VALID_EXTENSIONS = ('.py', '.js', '.ts', '.cpp', '.html', '.css', '.md', '.txt', '.jsx', '.tsx')

def _tokenize(text: str) -> list:
    """Batch 4.1: Sub-Word Tokenization."""
    raw_tokens = re.split(r'[^a-zA-Z0-9]+', text)
    tokens = []
    for token in raw_tokens:
        if not token:
            continue
        sub_tokens = re.sub(r'([a-z])([A-Z])', r'\1 \2', token).split()
        for sub in sub_tokens:
            tokens.extend([t.lower() for t in sub.split('_') if t])
    return tokens

def _chunk_python_file(filepath: str, content: str) -> list:
    """Batch 4.2: Deterministic AST Chunking for Python."""
    chunks = []
    lines = content.splitlines()
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                start = node.lineno
                end = node.end_lineno if hasattr(node, 'end_lineno') else start + 10
                chunk_code = "\n".join(lines[start-1:end])
                chunks.append({
                    "file": filepath,
                    "type": type(node).__name__,
                    "name": node.name,
                    "start": start,
                    "end": end,
                    "content": chunk_code
                })
    except Exception:
        pass 
    return chunks

def _chunk_generic_file(filepath: str, content: str, window=50, overlap=10) -> list:
    """Fallback chunker for non-Python or broken files."""
    chunks = []
    lines = content.splitlines()
    total = len(lines)
    for i in range(0, total, window - overlap):
        start = i + 1
        end = min(i + window, total)
        chunk_code = "\n".join(lines[start-1:end])
        chunks.append({
            "file": filepath,
            "type": "Block",
            "name": f"Lines {start}-{end}",
            "start": start,
            "end": end,
            "content": chunk_code
        })
    return chunks

def build_bm25_index(directory: str):
    """Batch 4.2: Build the chunked BM25 index."""
    chunks = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for fname in files:
            if fname.endswith(VALID_EXTENSIONS):
                path = os.path.join(root, fname)
                rel_path = os.path.relpath(path, directory)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    if fname.endswith('.py'):
                        file_chunks = _chunk_python_file(rel_path, content)
                        if not file_chunks:
                            file_chunks = _chunk_generic_file(rel_path, content)
                    else:
                        file_chunks = _chunk_generic_file(rel_path, content)
                    
                    chunks.extend(file_chunks)
                except Exception:
                    pass

    tokenized_corpus = [_tokenize(chunk["content"]) for chunk in chunks]
    bm25 = BM25Okapi(tokenized_corpus) if tokenized_corpus else None
    return {'bm25': bm25, 'chunks': chunks, 'timestamp': time.time()}

def semantic_search(query: str, index_data: dict, top_n=3) -> dict:
    """Batch 4.3: BM25 Context Routing with Metadata."""
    if not index_data or not index_data.get('bm25'):
        return {"context_string": "Error: Index not built or empty.", "metadata": []}
    
    bm25 = index_data['bm25']
    chunks = index_data['chunks']
    
    tokenized_query = _tokenize(query)
    scores = bm25.get_scores(tokenized_query)
    
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_n]
    
    results = []
    metadata = []
    for idx in top_indices:
        if scores[idx] > 0: 
            chunk = chunks[idx]
            res = f"--- File: {chunk['file']} ({chunk['type']}: {chunk['name']}) [Lines {chunk['start']}-{chunk['end']}] ---\n{chunk['content']}\n"
            results.append(res)
            metadata.append({
                "file": chunk['file'],
                "type": chunk['type'],
                "name": chunk['name'],
                "score": round(scores[idx], 2)
            })
            
    if not results:
        return {"context_string": f"No strong semantic matches found for '{query}'.", "metadata": []}
        
    return {"context_string": "\n".join(results), "metadata": metadata}

# --- BATCH 4.6 + ADVISOR UPDATE: SYMBOL RELATIONSHIP GRAPH ---
class SymbolGraphVisitor(ast.NodeVisitor):
    """Extracts lightweight navigational metadata: Imports and Call Graphs."""
    def __init__(self):
        self.imports = set()
        self.defines = {}  # symbol_name -> set of called functions
        self.current_scope = None

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.add(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self.imports.add(node.module)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        if not node.name.startswith('__'):
            self.defines[node.name] = set()
            old_scope = self.current_scope
            self.current_scope = node.name
            self.generic_visit(node)
            self.current_scope = old_scope

    def visit_ClassDef(self, node):
        self.defines[node.name] = set()
        old_scope = self.current_scope
        self.current_scope = node.name
        self.generic_visit(node)
        self.current_scope = old_scope

    def visit_Call(self, node):
        # If we are inside a function, record what other functions it calls
        if self.current_scope:
            if isinstance(node.func, ast.Name):
                self.defines[self.current_scope].add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                self.defines[self.current_scope].add(node.func.attr)
        self.generic_visit(node)

def _extract_architectural_metadata(filepath: str, content: str, heat_score: int = 0) -> str:
    """Compresses context based on heat, and injects the Call Graph and Conventions for hot files."""
    if not filepath.endswith('.py') or heat_score == 0:
        return ""
        
    try:
        tree = ast.parse(content)
        visitor = SymbolGraphVisitor()
        visitor.visit(tree)
                
        meta_string = ""

        # --- ADVISOR ROADMAP: CONVENTION LEARNING ---
        # Lightning-fast sniff for structural and stylistic patterns
        styles = []
        if "->" in content or "from typing" in content or "import typing" in content:
            styles.append("type_hints")
        if "async def" in content:
            styles.append("async")
        if '"""' in content or "'''" in content:
            styles.append("docstrings")
        if "logger." in content or "logging." in content:
            styles.append("logging")

        # Inject styles if the file is at least "Warm" (touched once)
        if styles and heat_score >= 1:
            meta_string += f" [Style: {', '.join(styles)}]"
        
        # High Heat (5+): Full Context + Relationship Graph
        if heat_score >= 5:
            if visitor.imports:
                meta_string += f" [Imports: {', '.join(list(visitor.imports)[:3])}]"
            if visitor.defines:
                def_strings = []
                for name, calls in list(visitor.defines.items())[:5]:
                    if calls:
                        # Limit to top 2 calls to keep tokens cheap
                        call_str = ", ".join(list(calls)[:2])
                        def_strings.append(f"{name} (calls: {call_str})")
                    else:
                        def_strings.append(name)
                meta_string += f" [Defines: {', '.join(def_strings)}]"
                
        # Low Heat (1-4): Partial Context (Names only, no call graph)
        else:
            if visitor.defines:
                meta_string += f" [Defines: {', '.join(list(visitor.defines.keys())[:2])}]"
                
        return meta_string
    except Exception:
        return ""

def get_project_skeleton(target_directory, file_heat=None):
    if file_heat is None:
        file_heat = {}
        
    valid_extensions = (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css")
    ignore_folders = {"node_modules", ".git", ".next", "dist", "build", "__pycache__", ".dave_cache", "venv", ".venv", "env"}
    
    skeleton = []
    index_data = {}
    
    for root, dirs, files in os.walk(target_directory):
        dirs[:] = [d for d in dirs if d not in ignore_folders]
        
        for f in files:
            if f.endswith(valid_extensions):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, target_directory).replace("\\", "/")
                
                heat = file_heat.get(rel_path, 0)
                index_data[rel_path] = full_path
                
                # --- ADAPTIVE RECONSTRUCTION ---
                # If file is "hot" (heat > 0), expand its metadata
                if heat > 0:
                    try:
                        with open(full_path, 'r', encoding='utf-8', errors='ignore') as file:
                            content = file.read()
                            lines = len(content.splitlines())
                            skeleton.append(f"📄 {rel_path} ({lines} lines) [HOT]")
                            
                            # Extract basic symbols (functions/classes)
                            symbols = re.findall(r'^(?:export\s+)?(?:def|class|function|const)\s+(\w+)', content, re.MULTILINE)
                            if symbols:
                                # Show top 5 symbols to keep it lean
                                skeleton.append(f"   ↳ Symbols: {', '.join(symbols[:5])}")
                    except Exception:
                        skeleton.append(f"📄 {rel_path}")
                else:
                    # Cold file: Just give the standard filename to save tokens
                    skeleton.append(f"📄 {rel_path}")
                    
    return "\n".join(skeleton), index_data

def map_codebase(directory, file_heat=None):
    skeleton, _ = get_project_skeleton(directory, file_heat)
    return skeleton