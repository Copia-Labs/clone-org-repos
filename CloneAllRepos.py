import requests  # must install this module with pip or package manager
from git import Repo  # must install gitpython module

def main():
    host = "https://app.copia.io"
    token = "0c28cda8d981cc98d7375fd2377ea8b5f2dc7cb1"
    org = "Cables123"

    # Page through repository search endpoint until we stop getting data
    page = 0
    repositories = []
    r = requests.get("{}/api/v1/repos/search?limit=3&page={}&token={}".format(host, page, token))
    while len(r.json()["data"]) and page < 1:
        repositories.extend(r.json()["data"])
        page = page + 1
        r = requests.get("{}/api/v1/repos/search?limit=3&page={}&token={}".format(host, page, token))

    # Loop through each repository returned, cloning it over SSH
    for repository in repositories:
        #Repo.clone_from(repository["ssh_url"], "repos/" + repository["full_name"])
        print(repository["ssh_url"])
        
    input("Continue...") # wait for user

if __name__ == "__main__":
    main()
