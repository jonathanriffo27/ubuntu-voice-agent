from ddgs import DDGS
ddgs = DDGS()
results = list(ddgs.text("tiempo actual en Puerto Natales", max_results=3))
print(results)
