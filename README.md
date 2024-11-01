# CloneAllRepos_by_Org
Script to clone all repos of an Org, and optionally, move them to another location

## How to use
The basic operation is by first configuring the INI file, and then running the EXE.

The INI files options are:

1. Host
    * This is the URL where your Org resides (i.e. https://app.copia.io)
2. Token
    * This is your Personal Access Token (see: https://docs.copia.io/docs/git-based-source-control/getting-started/creating-your-account/migrate#generating-access-tokens)
3. Organization
    * This is the name of the Org that you want to clone all of your repos.
4. MoveTo
    * Repos will be downloaded to a temporary folder.  By adding an absolute path here, you can move them repos to another location after cloning has been completed.  
    * When using this option, the initial temp location will be deleted after the repos are moved.
    * If the data already exists in the MoveTo location, it will be overwritten (remember, a cloned repo contain the full history, so keeping duplicates is redundant)