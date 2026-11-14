from .server import main

# The decoder's helper processes (spawn method) load this file again as
# "__mp_main__": only start Procyon when it is really the program being run.
if __name__ == "__main__":
    main()
