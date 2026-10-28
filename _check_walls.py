lines = open('C:/Users/joker/CodeProjects/Python/KingdomBot/bot3.py', encoding='utf-8').read().split('\n')
for i in range(len(lines)):
    s = lines[i].strip()
    if s.startswith("elif k == 'wall'") or s.startswith("elif k == 'shop'") or s.startswith("elif k == 'farm'") or s.startswith("elif k == 'portal'"):
        print(i+1, s[:60])
