@echo off
cd /d "C:\Users\Administrator\Desktop\Personal Jarvis\video"
call npx remotion render JarvisTutorial out/tutorial-4k.mp4 --scale 3 --crf 16 --concurrency=12 --log=error >> out\_render-tutorial-4k.log 2>&1
echo __DONE__ >> out\_render-tutorial-4k.log
