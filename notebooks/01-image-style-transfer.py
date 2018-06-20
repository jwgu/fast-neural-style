
# coding: utf-8

# # Fast Neural Style Transfer

# In[1]:


import time 

import matplotlib.pyplot as plt
import numpy as np
import torch
# For getting VGG model
import torchvision.models.vgg as vgg
import torch.utils.model_zoo as model_zoo
# Image transformation pipeline
from torchvision import transforms
from torch.utils.data import DataLoader
from torchvision import datasets
from torch.optim import Adam
from torch.autograd import Variable
from PIL import Image, ImageFile
from tqdm import tqdm_notebook

from fast_neural_style.transformer_net import TransformerNet
from fast_neural_style.utils import (
    gram_matrix, recover_image, tensor_normalizer
)
from fast_neural_style.loss_network import LossNetwork

get_ipython().run_line_magic('matplotlib', 'inline')
ImageFile.LOAD_TRUNCATED_IMAGES = True


# In[2]:


SEED = 1080
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed(SEED)
    torch.set_default_tensor_type('torch.cuda.FloatTensor')
    kwargs = {'num_workers': 4, 'pin_memory': True}
else:
    torch.set_default_tensor_type('torch.FloatTensor')
    kwargs = {}


# In[3]:


IMAGE_SIZE = 224
BATCH_SIZE = 4
DATASET = "../coco_2017/"
transform = transforms.Compose([
    transforms.Resize(IMAGE_SIZE), 
    transforms.CenterCrop(IMAGE_SIZE),
    transforms.ToTensor(), tensor_normalizer()])
# http://pytorch.org/docs/master/torchvision/datasets.html#imagefolder
train_dataset = datasets.ImageFolder(DATASET, transform)
# http://pytorch.org/docs/master/data.html#torch.utils.data.DataLoader
train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, **kwargs)


# In[4]:


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
with torch.no_grad():
    vgg_model = vgg.vgg16(pretrained=True)
    vgg_model.to(device)
    loss_network = LossNetwork(vgg_model)
loss_network.eval()
del vgg_model


# In[25]:


STYLE_IMAGE = "../style_images/rain-princess-cropped.jpg"
style_img = Image.open(STYLE_IMAGE).convert('RGB')
with torch.no_grad():
    style_img_tensor = transforms.Compose([
        transforms.Resize(IMAGE_SIZE* 2),
        transforms.ToTensor(),
        tensor_normalizer()]
    )(style_img).unsqueeze(0)
    # assert np.sum(style_img - recover_image(style_img_tensor.numpy())[0].astype(np.uint8)) < 3 * style_img_tensor.size()[2] * style_img_tensor.size()[3]
    style_img_tensor = style_img_tensor.to(device)


# Sanity check:

# In[26]:


plt.imshow(recover_image(style_img_tensor.cpu().numpy())[0])


# Precalculate gram matrices of the style image:

# In[27]:


# http://pytorch.org/docs/master/notes/autograd.html#volatile
with torch.no_grad():
    style_loss_features = loss_network(style_img_tensor)
    gram_style = [gram_matrix(y) for y in style_loss_features]


# In[11]:


style_loss_features._fields


# In[12]:


for i in range(len(style_loss_features)):
    print(i, np.mean(gram_style[i].cpu().numpy()))


# In[13]:


for i in range(len(style_loss_features)):
    print(i, np.mean(style_loss_features[i].cpu().numpy()))


# In[14]:


for i in range(len(style_loss_features)):
    print(i, gram_style[i].numel(), gram_style[i].size())


# ## Train the Transformer / Image Generator
# Utility function to save debug images during training:

# In[21]:


def save_debug_image(tensor_orig, tensor_transformed, filename):
    assert tensor_orig.size() == tensor_transformed.size()
    result = Image.fromarray(recover_image(tensor_transformed.cpu().numpy())[0])
    orig = Image.fromarray(recover_image(tensor_orig.cpu().numpy())[0])
    new_im = Image.new('RGB', (result.size[0] * 2 + 5, result.size[1]))
    new_im.paste(orig, (0,0))
    new_im.paste(result, (result.size[0] + 5,0))
    new_im.save(filename)


# In[22]:


get_ipython().run_line_magic('mkdir', '-p debug')


# In[5]:


transformer = TransformerNet()
mse_loss = torch.nn.MSELoss()
# l1_loss = torch.nn.L1Loss()
transformer.to(device)


# In[28]:


torch.set_default_tensor_type('torch.FloatTensor')

CONTENT_WEIGHT = 1
STYLE_WEIGHTS = np.array([1e3, 1e5, 1e3, 2e4]) * 5
LOG_INTERVAL = 100
REGULARIZATION = 1e-6

LR = 1e-3
optimizer = Adam(transformer.parameters(), LR)
transformer.train()
for epoch in range(2):
    agg_content_loss = 0.
    agg_style_loss = 0.
    agg_reg_loss = 0.
    count = 0
    for batch_id, (x, _) in tqdm_notebook(enumerate(train_loader), total=len(train_loader)):
        n_batch = len(x)
        count += n_batch
        optimizer.zero_grad()
        x = x.to(device)       
        y = transformer(x)
        with torch.no_grad():
            xc = x.detach()

        features_y = loss_network(y)
        features_xc = loss_network(xc)

        with torch.no_grad():
            f_xc_c = features_xc[1].detach()

        content_loss = CONTENT_WEIGHT * mse_loss(features_y[1], f_xc_c)

        reg_loss = REGULARIZATION * (
            torch.sum(torch.abs(y[:, :, :, :-1] - y[:, :, :, 1:])) + 
            torch.sum(torch.abs(y[:, :, :-1, :] - y[:, :, 1:, :])))

        style_loss = 0.
        for l, weight in enumerate(STYLE_WEIGHTS):
            gram_s = gram_style[l]
            gram_y = gram_matrix(features_y[l])
            style_loss += float(weight) * mse_loss(gram_y, gram_s.expand_as(gram_y))

        total_loss = content_loss + style_loss + reg_loss
        total_loss.backward()
        optimizer.step()

        agg_content_loss += content_loss
        agg_style_loss += style_loss
        agg_reg_loss += reg_loss

        if (batch_id + 1) % LOG_INTERVAL == 0:
            mesg = "{} [{}/{}] content: {:.6f}  style: {:.6f}  reg: {:.6f}  total: {:.6f}".format(
                        time.ctime(), count, len(train_dataset),
                        agg_content_loss / LOG_INTERVAL,
                        agg_style_loss / LOG_INTERVAL,
                        agg_reg_loss / LOG_INTERVAL,
                        (agg_content_loss + agg_style_loss + agg_reg_loss) / LOG_INTERVAL
                    )
            print(mesg)
            agg_content_loss = 0
            agg_style_loss = 0
            agg_reg_loss = 0
            transformer.eval()
            y = transformer(x)
            save_debug_image(x.data, y.data, "debug/{}_{}.png".format(epoch, count))
            transformer.train()


# In[29]:


import glob
fnames = glob.glob(DATASET + r"/*/*")
len(fnames)


# In[30]:


transformer = transformer.eval()


# In[35]:


img = Image.open(fnames[40]).convert('RGB')
transform = transforms.Compose([
                                
                                transforms.ToTensor(),
                                tensor_normalizer()])
img_tensor = transform(img).unsqueeze(0)
if torch.cuda.is_available():
    img_tensor = img_tensor.cuda()

img_output = transformer(Variable(img_tensor, volatile=True))
plt.imshow(recover_image(img_tensor.cpu().numpy())[0])


# In[36]:


Image.fromarray(recover_image(img_output.data.cpu().numpy())[0])


# In[37]:


img = Image.open(fnames[40]).convert('RGB')
transform = transforms.Compose([
                                transforms.Resize(IMAGE_SIZE),
                                transforms.ToTensor(),
                                tensor_normalizer()])
img_tensor = transform(img).unsqueeze(0)
if torch.cuda.is_available():
    img_tensor = img_tensor.cuda()

img_output = transformer(Variable(img_tensor, volatile=True))
plt.imshow(recover_image(img_tensor.cpu().numpy())[0])


# In[38]:


Image.fromarray(recover_image(img_output.data.cpu().numpy())[0])


# In[39]:


save_model_path = "model_rain_princess_cropped.pth"
torch.save(transformer.state_dict(), save_model_path)


# In[7]:


save_model_path = "model_rain_princess_cropped.pth"
transformer.load_state_dict(torch.load(save_model_path))


# In[8]:


img = Image.open("../content_images/amber.jpg").convert('RGB')
transform = transforms.Compose([
    transforms.Resize(512),
    transforms.ToTensor(),
    tensor_normalizer()])
img_tensor = transform(img).unsqueeze(0)
print(img_tensor.size())
if torch.cuda.is_available():
    img_tensor = img_tensor.cuda()

img_output = transformer(Variable(img_tensor, volatile=True))
plt.imshow(recover_image(img_tensor.cpu().numpy())[0])


# In[9]:


plt.imshow(recover_image(img_output.data.cpu().numpy())[0])


# In[10]:


img = Image.open("../content_images/amber.jpg").convert('RGB')
transform = transforms.Compose([
    transforms.Resize(224),
    transforms.ToTensor(),
    tensor_normalizer()])
img_tensor = transform(img).unsqueeze(0)
print(img_tensor.size())
if torch.cuda.is_available():
    img_tensor = img_tensor.cuda()

img_output = transformer(Variable(img_tensor, volatile=True))
plt.imshow(recover_image(img_output.data.cpu().numpy())[0])


# In[11]:


output_img = Image.fromarray(recover_image(img_output.data.cpu().numpy())[0])
output_img.save("amber.png")

