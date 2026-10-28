path = 'C:/Users/joker/CodeProjects/Python/KingdomBot/launcher.py'
lines = open(path, encoding='utf-8').read().split('\n')

# Find the second _read_output in App (the one referencing console_left_text)
second_read = None
for i in range(len(lines)):
    if 'def _read_output' in lines[i] and i > 1320:
        second_read = i
        break

# Find end of that method
end = None
for i in range(second_read + 1, len(lines)):
    if lines[i].startswith('    def ') or lines[i].startswith('class ') or lines[i].startswith('if __name__'):
        end = i
        break

print("removing _read_output duplicate lines %d to %d" % (second_read+1, end))
del lines[second_read:end]

# Also remove duplicate _append_text (the one after the removed _read_output)
# Find _append_text
for i in range(len(lines)):
    if 'def _append_text' in lines[i] and i > 1320:
        # Find end
        end2 = None
        for j in range(i + 1, len(lines)):
            if lines[j].startswith('    def ') or lines[j].startswith('class ') or lines[j].startswith('if __name__'):
                end2 = j
                break
        print("removing _append_text duplicate lines %d to %d" % (i+1, end2))
        del lines[i:end2]
        break

open(path, 'w', encoding='utf-8').write('\n'.join(lines))
print("done")
