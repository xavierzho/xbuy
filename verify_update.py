
import hashlib
import os

def get_file_hash(file_path):
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

print("验证更新结果：")
print("=" * 60)

# 检查更新后的 ext.bin
updated_ext = "d:\\py_out\\ext.bin"
user_sign = "C:\\Users\\Administrator\\Desktop\\新建文件夹 (3)\\sign_wasm.bin"

if os.path.exists(updated_ext) and os.path.exists(user_sign):
    hash_updated = get_file_hash(updated_ext)
    hash_user = get_file_hash(user_sign)
    
    print(f"项目 ext.bin:")
    print(f"  大小: {os.path.getsize(updated_ext):,} bytes")
    print(f"  SHA256: {hash_updated}")
    print()
    print(f"用户 sign_wasm.bin:")
    print(f"  大小: {os.path.getsize(user_sign):,} bytes")
    print(f"  SHA256: {hash_user}")
    print()
    
    if hash_updated == hash_user:
        print("✅ 完全相同！更新成功！")
    else:
        print("❌ 文件不同，更新失败！")
else:
    print("❌ 文件不存在")

print()
print(f"rush_bridge.exe 已重新编译:")
print(f"  大小: {os.path.getsize('d:\\py_out\\go_bridge\\rush_bridge.exe'):,} bytes")
