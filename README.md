#将所有的.py文件放在同一个文件夹下，在你的终端里键入如下命令：
py -m PyInstaller -F -w --clean mulgen_allinone.py
#会产生如下类似的输出：
36143 INFO: checking EXE
36143 INFO: Building EXE because EXE-00.toc is non existent
36143 INFO: Building EXE from EXE-00.toc
36145 INFO: Copying bootloader EXE to D:\Users\15432\Desktop\mulgen\dist\mulgen_allinone.exe
36247 INFO: Copying icon to EXE
36348 INFO: Copying 0 resources to EXE
36348 INFO: Embedding manifest in EXE
36401 INFO: Appending PKG archive to EXE
36497 INFO: Fixing EXE headers
37076 INFO: Building EXE from EXE-00.toc completed successfully.
37089 INFO: Build complete! The results are available in: D:\Users\15432\Desktop\mulgen\dist
#然后再在生成的dist文件夹里双击.exe程序即可食用。
#视频教程（B站）：https://www.bilibili.com/video/BV1XWi7B4EHe
