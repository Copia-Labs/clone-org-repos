# CloneAllRepos_by_Org
Script to clone all repos of an Org to a temporary local folder, and optionally, move them to another location.

## Pre-reqs
1. The EXE and INI file should be put into the same directory
2. Since this performs git opeations, git must be installed on the system, and the PATH should be updated for git access
	* To test, open a command prompt and type "git".  If it finds git and replies with a "usage" response, you are good.  If not, update your enviroment variable to include the location of the git executable in your PATH.

## Instructions
The basic operation is by first configuring the INI file, and then running the EXE.  If you run it without configuring the INI, it should ask you for the required information (token and org name).  

In v1.0, there is limited error checking on the INI data, so it will fail if it cannot find the INI, the Host URL is incorrect, or you get the Org Name wrong, as some examples.

## Configuration File
The INI files options are:

1. **Host**
    * This is the URL where your Org resides
	* *Sample Format: https://app.copia.io*
2. **Token**
    * This is your Personal Access Token (see: https://docs.copia.io/docs/git-based-source-control/getting-started/creating-your-account/migrate#generating-access-tokens)
	* *Sample Format: 0c28xda8x981xx98d7415xx2377ea8b5x5dc7cx7*
3. **Organization**
    * This is the name of the Org that you want to clone all of your repos.
	* *Sample Format: Cables123*
4. **MoveTo**
    * Repos will be downloaded to a temporary folder.  By adding an absolute path here, you can move them repos to another location after cloning has been completed.  
    * When using this option, the initial temp location will be deleted after the repos are moved.
    * If the data already exists in the MoveTo location, it will be overwritten (remember, a cloned repo contain the full history, so keeping duplicates is redundant)
	* *Sample Format: c:\temp\data*