import os
from pathlib import Path
import re
import json
import string
import shutil
import tarfile
import nbformat
import logging
from pathlib import Path
from traitlets.config import Config
from nbconvert.writers import FilesWriter
from nbconvert import SlidesExporter, MarkdownExporter
from nbconvert.preprocessors import ExecutePreprocessor, CellExecutionError
from azure.cosmos import CosmosClient
from azure.storage.blob import BlobServiceClient
import azure.functions as func


def main(msg: func.QueueMessage) -> None:
    def get_path(s):
        return str(Path(s).expanduser().absolute().resolve())

    def dir_to_list(dirname):
        data = []
        for name in sorted(os.listdir(dirname)):
            dct = {}
            dct['name'] = name
            dct['path'] = get_path(os.path.join(dirname, name))
            full_path = os.path.join(dirname, name)
            if os.path.isfile(full_path):
                data.append(dct)
        return data

    def format_name(s, i):
        a = re.sub("ch\d", f"{i}.", s)
        b = string.capwords(a.replace("_", " "))
        return b

    database = CosmosClient.from_connection_string(
        os.environ['manimbooksdata_DOCUMENTDB']).get_database_client('books')
    db_container = database.get_container_client('bookData')
    blob = BlobServiceClient.from_connection_string(
        os.environ['AzureWebJobsStorage'])
    HOME_DIR = os.path.dirname(os.path.realpath(__file__))
    script_dir = HOME_DIR + "/convert"
    bookId = msg.get_body().decode('utf-8')
    folder = HOME_DIR + "/temp/" + bookId + "/read"

    if not (os.path.isdir(folder)):
        os.makedirs(folder, exist_ok=True)
    query = "SELECT * FROM books WHERE books.id = '" + bookId + "'"
    items = list(db_container.query_items(
        query=query,
        enable_cross_partition_query=True
    ))
    if len(items) == 0:
        return
    item = items[0]
    book = item['bookName']
    author = item['author']
    cover_name = item['cover']
    container = blob.get_container_client(bookId)
    blob_list = container.list_blobs()

    def changestatus(status):
        query = "SELECT * FROM books WHERE books.id = '" + bookId + "'"
        items = list(db_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        if len(items) == 0:
            return False
        item = items[0]
        item['status'] = status
        db_container.upsert_item(item)
        return True

    def download_blob(blob_client, destination_file):
        with open(destination_file, "wb") as my_blob:
            blob_data = blob_client.download_blob()
            blob_data.readinto(my_blob)

    for blob in blob_list:
        if "/" in "{}".format(blob.name):
            head, tail = os.path.split("{}".format(blob.name))
            if not (os.path.isdir(folder + "/" + head)):
                os.makedirs(folder + "/" + head, exist_ok=True)
        blob_client = container.get_blob_client(blob.name)
        download_blob(blob_client, folder + "/"+blob.name)

    # custom configuration for nbconvert
    c = Config()
    c.TemplateExporter.extra_template_basedirs
    my_templates = script_dir + '/templates'
    c.TemplateExporter.extra_template_basedirs = [my_templates]
    c.TemplateExporter.exclude_input = True
    c.SlidesExporter.theme = 'dark'
    c.SlidesExporter.reveal_theme = 'night'
    c.SlidesExporter.reveal_scroll = True
    c.FilesWriter.build_directory = f"{script_dir}/.cache/{bookId}"

    # initialize cache output folder
    if not os.path.exists(f"{script_dir}/.cache/"):
        os.mkdir(f"{script_dir}/.cache/")
    if not os.path.exists(c.FilesWriter.build_directory):
        os.mkdir(c.FilesWriter.build_directory)

    chapters = []

    i = 1
    for notebook in dir_to_list(get_path(folder)):
        if notebook['name'].rsplit('.', 1)[1].lower() != 'ipynb':
            continue
        dct = {}
        os.chdir(c.FilesWriter.build_directory)
        changestatus("Converting " + notebook['name'])
        shutil.copy2(notebook['path'], c.FilesWriter.build_directory)
        filename = format_name(str(notebook['name']).replace(".ipynb", ""), i)
        dct['name'] = filename
        i += 1

        # execute (render) the contents of the notebook
        ep = ExecutePreprocessor(timeout=1800)
        nb = nbformat.read(notebook['path'], nbformat.NO_CONVERT)
        try:
            ep.preprocess(nb)
        except CellExecutionError as e:
            changestatus("Error in " + notebook['name'])
            logging.error(
                "Error in " + notebook['name'] + e, exc_info=True)
            return False

        # convert the notebook to slides
        slides = SlidesExporter(config=c, template_name="reveal.js")
        (output, resources) = slides.from_notebook_node(nb)
        fw = FilesWriter(config=c)
        fw.write(output, resources, notebook_name=filename)
        dct['slides'] = filename + ".slides.html"

        # convert the notebook to markdown, copy it to texme html template
        shutil.copy2(f"{script_dir}/templates/scroll.html",
                     f"{filename}.html")
        scroll = MarkdownExporter(config=c)
        (output, resources) = scroll.from_notebook_node(nb)
        fw = FilesWriter(config=c)
        fw.write(output, resources, notebook_name=filename)
        with open(f"{filename}.md", "r") as f, open(f"{filename}.html", "a+") as g:
            g.write(f.read())
            os.remove(f"{filename}.md")
        dct['md'] = filename + ".html"
        chapters.append(dct)

    # create index.json file
    index = {
        "id": bookId,
        "author": author,
        "title": book,
        "chapters": chapters,
        "cover": cover_name
    }
    open("index.json", "w").write(json.dumps(index, indent=4))
    # create tarball
    os.chdir(get_path(f"{c.FilesWriter.build_directory}") + "/../..")

    def make_tarfile(output_filename, source_dir):
        with tarfile.open(output_filename, "w:gz") as tar:
            tar.add(source_dir, arcname=os.path.basename(source_dir))
        tar.close()

    changestatus("Creating book")
    make_tarfile(f"{book}.mbook", f"./.cache/{bookId}")
    shutil.move(f"{book}.mbook", folder)
    file = tarfile.open(f"{folder}/{book}.mbook")
    file.extractall(folder)
    shutil.rmtree(f"./.cache/{bookId}")

    path_remove = HOME_DIR + "/temp/" + bookId
    for r, d, f in os.walk(folder):
        if f:
            for file in f:
                file_path_on_azure = os.path.join(
                    r, file).replace(path_remove, "")
                file_path_on_local = os.path.join(r, file)

                blob_client = container.get_blob_client(
                    file_path_on_azure)

                with open(file_path_on_local, 'rb') as data:
                    blob_client.upload_blob(data)
    changestatus("Cleaning Up")
    shutil.rmtree(HOME_DIR + "/temp/" + bookId)
    changestatus("Done")

    return
