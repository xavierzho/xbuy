
import hashlib
import os

def get_file_hash(file_path):
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

files_to_compare = [
    ("d:\\py_out\\runtime.wasm", "项目 runtime.wasm"),
    ("C:\\Users\\Administrator\\Desktop\\新建文件夹 (3)\\stub.wasm", "用户 stub.wasm"),
    ("d:\\py_out\\ext.bin", "项目 ext.bin"),
    ("C:\\Users\\Administrator\\Desktop\\新建文件夹 (3)\\sign_wasm.bin", "用户 sign_wasm.bin"),
]

print("文件对比结果：")
print("=" * 60)
for file_path, desc in files_to_compare:
    if os.path.exists(file_path):
        file_hash = get_file_hash(file_path)
        file_size = os.path.getsize(file_path)
        print(f"{desc}:")
        print(f"  文件大小: {file_size:,} bytes")
        print(f"  SHA256:   {file_hash}")
    else:
        print(f"{desc}: 文件不存在")
    print()

print("\n对比相同的文件：")
print("-" * 60)

# 检查是否有相同的哈希值
hashes = {}
for file_path, desc in files_to_compare:
    if os.path.exists(file_path):
        file_hash = get_file_hash(file_path)
        if file_hash in hashes:
            print(f"✓ {desc} 与 {hashes[file_hash]} 完全相同！")
        else:
            hashes[file_hash] = desc
