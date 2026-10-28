lines = open('C:/Users/joker/CodeProjects/Python/KingdomBot/launcher.py', encoding='utf-8').read().split('\n')
for i in range(len(lines)):
    if 'btn_frame' in lines[i]:
        print(i+1, lines[i].strip()[:70])
