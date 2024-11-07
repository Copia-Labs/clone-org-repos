import getpass
from InquirerPy import prompt   # must install InquirerPy module
import configparser             # must install configparser module
import pprint
import os
import stat
import sys
import shutil
import requests                 # for http requests
from git import Repo            # must install gitpython module

import shutil
import os

def remove_readonly(func, path, excinfo):
    """Change the file permission to writable and retry the removal."""
    os.chmod(path, stat.S_IWRITE)  # Change the file attribute to writable
    func(path)  # Retry the removal

def delete_folder(folder_path):
    """Delete the specified folder and handle exceptions."""
    try:
        shutil.rmtree(folder_path, onerror=remove_readonly)
        print(f"Successfully removed the folder: {folder_path}")
    except PermissionError as e:
        print(f"Permission denied: {e}")
    except FileNotFoundError:
        print(f"The folder does not exist: {folder_path}")
    except Exception as e:
        print(f"An error occurred: {e}")

def copy_folder_contents(src_folder, dst_folder):
    # Copy each item from the source to the destination
    for item in os.listdir(src_folder):
        src_path = os.path.join(src_folder, item)
        dst_path = os.path.join(dst_folder, item)
        
        if os.path.isdir(src_path):
            # Recursively copy subdirectories
            shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        else:
            # Copy files
            shutil.copy2(src_path, dst_path)
            
def is_folder_empty(folder_path):
    return not os.listdir(folder_path)
            
def main():

    #get path and dir of the script
    script_path = os.path.abspath(__file__)
    script_dir = os.path.dirname(script_path)
    
    #Initialize the configparser to read INI
    config = configparser.ConfigParser()
    config.read('./clone_org_repos.ini')
    
    #check the tmp folder exists and clear it
    tmp_folder = os.path.join(script_dir, 'repos')
    if os.path.exists(tmp_folder) and os.path.isdir(tmp_folder):
        delete_folder(tmp_folder)
        os.makedirs(tmp_folder)
    
    #Set the host, default in the INI is https://app.copia.io
    host = config['DEFAULT']['Host']

    # Read INI or prompt the user for their Copia Auth Token
    if len(config['DEFAULT']['Token'])>0:
        auth_token = config['DEFAULT']['Token']
    else:    
        auth_token = getpass.getpass(prompt="Enter your Copia Auth Token: ") 
    
    # Read INI or prompt the user for their Copia Org
    if len(config['DEFAULT']['Organization'])>0:
        selected_org = config['DEFAULT']['Organization']
    else:    
        # Get the list of organizations for the user, in case belongs to more than one
        response = requests.get('https://app.copia.io/api/v1/user/orgs', headers={'Authorization': f'token {auth_token}'})

        # Ensure the response is valid
        if response.status_code != 200:
            print("Error retrieving organizations.")
            exit()
            
        # Parse the response JSON
        orgs = response.json()
        org_names = [org['username'] for org in orgs]

        # Ask the user to select an organization
        org_question = [
            {
                'type': 'list',
                'name':'org',
                'message': 'Select an organization:',
                'choices': org_names,
            },
        ]
        org_answer = prompt(org_question)
        selected_org = org_answer['org']

    # Get list of repos for the user/org combo
    page = 0
    repositories = []
    r = requests.get(f"{host}/api/v1/orgs/{selected_org}/repos?limit=3&page={page}&token={auth_token}")
    repositories =(r.json())
    
    num_repos = len(repositories)
    count_repos = 1
    
    # Loop through each repository returned, cloning it to the cwd in a tmp folder
    for repository in repositories:
        print("Cloning " + str(count_repos) + " of " + str(num_repos) + ": " + repository["full_name"] + " into " + tmp_folder)
        Repo.clone_from(repository["clone_url"], os.path.join(tmp_folder, repository["full_name"]))
        #print(repository["clone_url"]) #for testing
        count_repos += 1
        if len(config['DEFAULT']['RepoCount']) > 0:
            break
        
    # Move data to new location if configured in the INI
    dst_folder = config['DEFAULT']['MoveTo']
    if len(dst_folder)>0:
        if os.path.exists(os.path.join(dst_folder, selected_org)) and os.path.isdir(os.path.join(dst_folder, selected_org)):
            print('Deleting target location...')
            delete_folder(os.path.join(dst_folder, selected_org))
        print('Moving data to destination...')
        copy_folder_contents(tmp_folder, dst_folder)
        print('Deleting temp folder...')
        delete_folder(tmp_folder)
        
    print('Complete')
        
if __name__ == "__main__":
    main()
