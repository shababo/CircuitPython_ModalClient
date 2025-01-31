# import sys

# import modal

# app = modal.App("example-hello-world")

# @app.function()
# def f(i):
#     if i % 2 == 0:
#         print("hello", i)
#     else:
#         print("world", i, file=sys.stderr)

#     return i * i

# @app.local_entrypoint()
# def main():
#     # run the function locally
#     print(f.local(1000))

#     # run the function remotely on Modal
#     print(f.remote(1000))

#     # run the function in parallel and remotely on Modal
#     total = 0
#     for ret in f.map(range(200)):
#         total += ret

#     print(total)


# if __name__ == "__main__":
#     main()

import modal

app = modal.App("example-get-started")


@app.function()
def square(x):
    print("This code is running on a remote worker!")
    return x**2


@app.local_entrypoint()
def main():
    with app.run():
        print("the square is", square.remote(42))


if __name__ == "__main__":
    main()
