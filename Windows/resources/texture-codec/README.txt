Player Studio ETC2 codec: fixed wolfpld/etcpak commit
3d716e1550023dc5ff8b602636af8b76584e0b66, BSD-3-Clause.
FreeBSD float-FMA arithmetic, BSD-2-Clause.
Complete license notices are in the licenses subdirectory.

Windows: windows-x86_64/studio_etc2.dll
Android: independently packaged lib/<ABI>/libstudio_etc2.so (arm64-v8a/x86_64).
No external C/C++ runtime is required by these codec libraries.

Source/compiler/artifact manifests accompany the binaries; upstream source
and reproducible build script are in the source distribution. Colour uses
opt in to ETC2. Normal/SRO/unverified uses and failed independent quality
checks retain exact BGRA8 at the original authored dimensions/mip strategy.
