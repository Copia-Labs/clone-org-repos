# CloneAllRepos_by_Org
A Script to perform a shallow clone (*the latest commit*) of all repos of an Org to a pre-defined local folder.  This folder is called "repos" and will be created in the same location in which the script is executed.  The script is configured by an `ini` file, which allows (*among other things*) to move the data to another location.

#### Notes
- A shallow clone is performed due to the umber of LFS objects (*i.e. binary files*) that are typically used in a Copia repo.  Downloading all LFS objects for the entire history, for all repos may be very large.
- If your goal is to have a backup of the Copia data, mirroring your data to GitHub, Gitlab or Azure may be another way to accomplish this.

## Pre-Reqs
1. The EXE and INI file should be put into the same directory
2. Since this performs git operations, git must be installed on the system, and the PATH should be updated for git access
	* To test, open a command prompt and type "git".  If it finds git and replies with a "usage" response, you are good.  If not, update your environment variable to include the location of the git executable in your PATH.

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
4. **StopAfter**
    * This allows you to limit the number of repos you are cloning for testing  
    * It is set to 1 by default for testing
    * Change this value to 0 to clone all repos
	* *Sample Format: 0*
4. **MoveTo**
    * Repos will be downloaded to a temporary folder called "repos" in the location that the script is executed.  By adding an absolute path here, you can move them repos to another location after cloning has been completed.  
    * When using this option, the initial temp location will be deleted after the repos are moved.
    * If the data already exists in the `MoveTo` location, it will be overwritten (remember, a cloned repo contain the full history, so keeping duplicates is redundant)
	* *Sample Format: c:\temp\data*
