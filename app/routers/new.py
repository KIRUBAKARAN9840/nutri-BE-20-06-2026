


def two_sums(nums):
    duplicate_checking={}
    for index,item in enumerate(nums):
        if index==0:
            duplicate_checking[item]=index
        else:
            if item in duplicate_checking:
                return True
            
            else:
                duplicate_checking[item]=index
                

        
            
          
nums=[1,2,3,4,5,4,1]

data= two_sums(nums)
print("data is",data)
     


