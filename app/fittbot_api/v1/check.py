
def element_checking(a,t):
    if len(a)!=len(t):
        return False

    freq={}
    for val in t:
        freq[val]=freq.get(val,0)+1

    for val in a:
        square=val*val
        if freq.get(square,0)==0:
            return False
        freq[square]-=1

    return True
        

actual=[5,-2,3,1,7]
target=[1,4,49,9,25]

result=element_checking(actual,target)

print("result is",result)