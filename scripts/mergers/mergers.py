from linecache import clearcache
import random
import os
import gc
import numpy as np
import os.path
import re
import torch
import tqdm
import datetime
import csv
import json
import torch.nn as nn
import scipy.ndimage
from scipy.ndimage.filters import median_filter as filter
from PIL import Image, ImageFont, ImageDraw
from tqdm import tqdm
from modules import shared, processing, sd_models, sd_vae, images, sd_samplers,scripts
from modules.ui import  plaintext_to_html
from modules.shared import opts
from modules.processing import create_infotext,Processed
from modules.sd_models import  load_model,checkpoints_loaded
from scripts.mergers.model_util import usemodelgen,filenamecutter,savemodel
from math import ceil
from multiprocessing import cpu_count
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed

from inspect import currentframe

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

stopmerge = False

def freezemtime():
    global stopmerge
    stopmerge = True

mergedmodel=[]
FINETUNEX = ["IN","OUT","OUT2","CONT","COL1","COL2","COL3"]
TYPESEG = ["none","alpha","beta (if Triple or Twice is not selected,Twice automatically enable)","alpha and beta","seed", "mbw alpha","mbw beta","mbw alpha and beta", "model_A","model_B","model_C","pinpoint blocks (alpha or beta must be selected for another axis)","elemental","add elemental","pinpoint element","effective elemental checker","adjust","pinpoint adjust (IN,OUT,OUT2,CONT,COL1,COL2,,COL3)","calcmode","prompt","random"]
TYPES = ["none","alpha","beta","alpha and beta","seed", "mbw alpha ","mbw beta","mbw alpha and beta", "model_A","model_B","model_C","pinpoint blocks","elemental","add elemental","pinpoint element","effective","adjust","pinpoint adjust","calcmode","prompt","random"]
MODES=["Weight" ,"Add" ,"Triple","Twice"]
SAVEMODES=["save model", "overwrite"]
#type[0:aplha,1:beta,2:seed,3:mbw,4:model_A,5:model_B,6:model_C]
#msettings=[0 weights_a,1 weights_b,2 model_a,3 model_b,4 model_c,5 base_alpha,6 base_beta,7 mode,8 useblocks,9 custom_name,10 save_sets,11 id_sets,12 wpresets]
#id sets "image", "PNG info","XY grid"

hear = False
hearm = False
non4 = [None]*4

def caster(news,hear):
    if hear: print(news)

def casterr(*args,hear=hear):
    if hear:
        names = {id(v): k for k, v in currentframe().f_back.f_locals.items()}
        print('\n'.join([names.get(id(arg), '???') + ' = ' + repr(arg) for arg in args]))
    
  #msettings=[weights_a,weights_b,model_a,model_b,model_c,device,base_alpha,base_beta,mode,loranames,useblocks,custom_name,save_sets,id_sets,wpresets,deep]  
def smergegen(weights_a,weights_b,model_a,model_b,model_c,base_alpha,base_beta,mode,
                       calcmode,useblocks,custom_name,save_sets,id_sets,wpresets,deep,tensor,bake_in_vae,
                       esettings,
                       prompt,nprompt,steps,sampler,cfg,seed,w,h,
                       hireson,hrupscaler,hr2ndsteps,denoise_str,hr_scale,
                       s_prompt,s_nprompt,s_steps,s_sampler,s_cfg,s_seed,s_w,s_h,batch_size,
                       lmode,lsets,llimits_u,llimits_l,lseed,lserial,lcustom,lround,
                       currentmodel,imggen):

    lucks = {"on":False, "mode":lmode,"set":lsets,"upp":llimits_u,"low":llimits_l,"seed":lseed,"num":lserial,"cust":lcustom,"round":int(lround)}
    deepprint  = True if "print change" in esettings else False

    result,currentmodel,modelid,theta_0,metadata = smerge(
                        weights_a,weights_b,model_a,model_b,model_c,base_alpha,base_beta,mode,calcmode,
                        useblocks,custom_name,save_sets,id_sets,wpresets,deep,tensor,bake_in_vae,deepprint,lucks
                        )

    if "ERROR" in result or "STOPPED" in result: 
        return result,"not loaded",*non4

    usemodelgen(theta_0,model_a,currentmodel)

    save = True if SAVEMODES[0] in save_sets else False

    result = savemodel(theta_0,currentmodel,custom_name,save_sets,model_a,metadata) if save else "Merged model loaded:"+currentmodel
    del theta_0
    gc.collect()

    if imggen :
        images = simggen(prompt,nprompt,steps,sampler,cfg,seed,w,h,hireson,hrupscaler,hr2ndsteps,denoise_str,hr_scale,
                                    s_prompt,s_nprompt,s_steps,s_sampler,s_cfg,s_seed,s_w,s_h,batch_size,currentmodel,id_sets,modelid)
        return result,currentmodel,*images[:4]
    else:
        return result,currentmodel

NUM_INPUT_BLOCKS = 12
NUM_MID_BLOCK = 1
NUM_OUTPUT_BLOCKS = 12
NUM_TOTAL_BLOCKS = NUM_INPUT_BLOCKS + NUM_MID_BLOCK + NUM_OUTPUT_BLOCKS
BLOCKID=["BASE","IN00","IN01","IN02","IN03","IN04","IN05","IN06","IN07","IN08","IN09","IN10","IN11","M00","OUT00","OUT01","OUT02","OUT03","OUT04","OUT05","OUT06","OUT07","OUT08","OUT09","OUT10","OUT11"]

RANDMAP = [0,50,100] #alpha,beta,elements

statistics = {"sum":{},"mean":{},"max":{},"min":{}}

def smerge(weights_a,weights_b,model_a,model_b,model_c,base_alpha,base_beta,mode,calcmode,
                useblocks,custom_name,save_sets,id_sets,wpresets,deep,fine,bake_in_vae,deepprint,lucks):
    caster("merge start",hearm)
    global hear,mergedmodel,stopmerge,statistics
    stopmerge = False

    gc.collect()

    # for from file
    if type(useblocks) is str:
        useblocks = True if useblocks =="True" else False
    if type(base_alpha) == str:base_alpha = float(base_alpha)
    if type(base_beta) == str:base_beta  = float(base_beta)

    #random
    if lucks != {}:
        if lucks["seed"] == -1: lucks["ceed"] = str(random.randrange(4294967294))
        else: lucks["ceed"] = lucks["seed"] 
    else: lucks["ceed"]  = 0
    np.random.seed(int(lucks["ceed"]))
    randomer = np.random.rand(2500)

    weights_a,deep = randdealer(weights_a,randomer,0,lucks,deep)
    weights_b,_ = randdealer(weights_b,randomer,1,lucks,None)

    weights_a_orig = weights_a
    weights_b_orig = weights_b

    # preset to weights
    if wpresets != False and useblocks:
        weights_a = wpreseter(weights_a,wpresets)
        weights_b = wpreseter(weights_b,wpresets)

    # mode select booleans
    save = True if SAVEMODES[0] in save_sets else False
    usebeta = MODES[2] in mode or MODES[3] in mode or "tensor" in calcmode
    save_metadata = "save metadata" in save_sets
    metadata = {"format": "pt"}

    if not useblocks:
        weights_a = weights_b = ""
    #for save log and save current model
    mergedmodel =[weights_a,weights_b,
                            hashfromname(model_a),hashfromname(model_b),hashfromname(model_c),
                            base_alpha,base_beta,mode,useblocks,custom_name,save_sets,id_sets,deep,calcmode,lucks["ceed"],fine].copy()

    model_a = namefromhash(model_a)
    model_b = namefromhash(model_b)
    model_c = namefromhash(model_c)

    #adjust
    if fine:
        fine = [float(t) for t in fine.split(",")]
        fine = fineman(fine)

    caster(mergedmodel,False)

    if calcmode == calcmode == "trainDifference" and "Add" not in mode:
        print(f"{bcolors.WARNING}Mode changed to add difference{bcolors.ENDC}")
        mode = "Add"

    result_is_inpainting_model = False
    result_is_instruct_pix2pix_model = False

    #elementals
    if len(deep) > 0:
        deep = deep.replace("\n",",")
        deep = deep.replace(calcmode+",","")
        deep = deep.split(",")

    #format check
    if model_a =="" or model_b =="" or ((not MODES[0] in mode) and model_c=="") : 
        return "ERROR: Necessary model is not selected",*non4
    
    #for MBW text to list
    if useblocks:
        weights_a_t=weights_a.split(',',1)
        weights_b_t=weights_b.split(',',1)
        base_alpha  = float(weights_a_t[0])    
        weights_a = [float(w) for w in weights_a_t[1].split(',')]
        caster(f"from {weights_a_t}, alpha = {base_alpha},weights_a ={weights_a}",hearm)
        if len(weights_a) != 25:return f"ERROR: weights alpha value must be {26}.",*non4
        if usebeta:
            base_beta = float(weights_b_t[0]) 
            weights_b = [float(w) for w in weights_b_t[1].split(',')]
            caster(f"from {weights_b_t}, beta = {base_beta},weights_a ={weights_b}",hearm)
            if len(weights_b) != 25: return f"ERROR: weights beta value must be {26}.",*non4
        
    caster("model load start",hearm)

    print(f"  model A  \t: {model_a}")
    print(f"  model B  \t: {model_b}")
    print(f"  model C  \t: {model_c}")
    print(f"  alpha,beta\t: {base_alpha,base_beta}")
    print(f"  weights_alpha\t: {weights_a}")
    print(f"  weights_beta\t: {weights_b}")
    print(f"  mode\t\t: {mode}")
    print(f"  MBW \t\t: {useblocks}")
    print(f"  CalcMode \t: {calcmode}")
    print(f"  Elemental \t: {deep}")
    print(f"  Weights Seed\t: {lucks['ceed']}")
    print(f"  Adjust \t: {fine}")

    theta_1=load_model_weights_m(model_b,False,True,save).copy()

    if MODES[1] in mode:#Add
        if stopmerge: return "STOPPED", *non4
        if calcmode == "trainDifference":
            theta_2 = load_model_weights_m(model_c,True,False,save).copy()
        else:
            theta_2 = load_model_weights_m(model_c,False,False,save).copy()
            for key in tqdm(theta_1.keys()):
                if 'model' in key:
                    if key in theta_2:
                        t2 = theta_2.get(key, torch.zeros_like(theta_1[key]))
                        theta_1[key] = theta_1[key]- t2
                    else:
                        theta_1[key] = torch.zeros_like(theta_1[key])
            del theta_2

    if stopmerge: return "STOPPED", *non4
    
    if  "tensor" in calcmode:
        theta_t = load_model_weights_m(model_a,True,False,save).copy()
        theta_0 ={}
        for key in theta_t:
            theta_0[key] = theta_t[key].clone()
        del theta_t
    else:
        theta_0=load_model_weights_m(model_a,True,False,save).copy()

    if MODES[2] in mode or MODES[3] in mode:#Tripe or Twice
        theta_2 = load_model_weights_m(model_c,False,False,save).copy()
    else:
        if calcmode != "trainDifference":
            theta_2 = {}

    alpha = base_alpha
    beta = base_beta

    re_inp = re.compile(r'\.input_blocks\.(\d+)\.')  # 12
    re_mid = re.compile(r'\.middle_block\.(\d+)\.')  # 1
    re_out = re.compile(r'\.output_blocks\.(\d+)\.') # 12

    chckpoint_dict_skip_on_merge = ["cond_stage_model.transformer.text_model.embeddings.position_ids"]
    count_target_of_basealpha = 0

    if calcmode =="cosineA": #favors modelA's structure with details from B
        if stopmerge: return "STOPPED", *non4
        sim = torch.nn.CosineSimilarity(dim=0)
        sims = np.array([], dtype=np.float64)
        for key in (tqdm(theta_0.keys(), desc="Stage 0/2")):
            # skip VAE model parameters to get better results
            if "first_stage_model" in key: continue
            if "model" in key and key in theta_1:
                theta_0_norm = nn.functional.normalize(theta_0[key].to(torch.float32), p=2, dim=0)
                theta_1_norm = nn.functional.normalize(theta_1[key].to(torch.float32), p=2, dim=0)
                simab = sim(theta_0_norm, theta_1_norm)
                sims = np.append(sims,simab.numpy())
        sims = sims[~np.isnan(sims)]
        sims = np.delete(sims, np.where(sims<np.percentile(sims, 1 ,method = 'midpoint')))
        sims = np.delete(sims, np.where(sims>np.percentile(sims, 99 ,method = 'midpoint')))

    if calcmode =="cosineB": #favors modelB's structure with details from A
        if stopmerge: return "STOPPED", *non4
        sim = torch.nn.CosineSimilarity(dim=0)
        sims = np.array([], dtype=np.float64)
        for key in (tqdm(theta_0.keys(), desc="Stage 0/2")):
            # skip VAE model parameters to get better results
            if "first_stage_model" in key: continue
            if "model" in key and key in theta_1:
                simab = sim(theta_0[key].to(torch.float32), theta_1[key].to(torch.float32))
                dot_product = torch.dot(theta_0[key].view(-1).to(torch.float32), theta_1[key].view(-1).to(torch.float32))
                magnitude_similarity = dot_product / (torch.norm(theta_0[key].to(torch.float32)) * torch.norm(theta_1[key].to(torch.float32)))
                combined_similarity = (simab + magnitude_similarity) / 2.0
                sims = np.append(sims, combined_similarity.numpy())
        sims = sims[~np.isnan(sims)]
        sims = np.delete(sims, np.where(sims < np.percentile(sims, 1, method='midpoint')))
        sims = np.delete(sims, np.where(sims > np.percentile(sims, 99, method='midpoint')))

    keyratio = []
    key_and_alpha = {}

    for num, key in enumerate(tqdm(theta_0.keys(), desc="Stage 1/2") if not False else theta_0.keys()):
        if stopmerge: return "STOPPED", *non4
        if "model" in key and key in theta_1:
            if calcmode == "trainDifference":
                if key not in theta_2:
                    continue
            else:
               if usebeta and (not key in theta_2) and (not theta_2 == {}) :
                    continue

            weight_index = -1
            current_alpha = alpha
            current_beta = beta

            if key in chckpoint_dict_skip_on_merge:
                continue

            a = theta_0[key]
            b = theta_1[key]

            # this enables merging an inpainting model (A) with another one (B);
            # where normal model would have 4 channels, for latenst space, inpainting model would
            # have another 4 channels for unmasked picture's latent space, plus one channel for mask, for a total of 9
            if a.shape != b.shape and a.shape[0:1] + a.shape[2:] == b.shape[0:1] + b.shape[2:]:
                if a.shape[1] == 4 and b.shape[1] == 9:
                    raise RuntimeError("When merging inpainting model with a normal one, A must be the inpainting model.")
                if a.shape[1] == 4 and b.shape[1] == 8:
                    raise RuntimeError("When merging instruct-pix2pix model with a normal one, A must be the instruct-pix2pix model.")

                if a.shape[1] == 8 and b.shape[1] == 4:#If we have an Instruct-Pix2Pix model...
                    result_is_instruct_pix2pix_model = True
                else:
                    assert a.shape[1] == 9 and b.shape[1] == 4, f"Bad dimensions for merged layer {key}: A={a.shape}, B={b.shape}"
                    result_is_inpainting_model = True

            # check weighted and U-Net or not
            if weights_a is not None and 'model.diffusion_model.' in key:
                # check block index
                weight_index = -1

                if 'time_embed' in key:
                    weight_index = 0                # before input blocks
                elif '.out.' in key:
                    weight_index = NUM_TOTAL_BLOCKS - 1     # after output blocks
                else:
                    m = re_inp.search(key)
                    if m:
                        inp_idx = int(m.groups()[0])
                        weight_index = inp_idx
                    else:
                        m = re_mid.search(key)
                        if m:
                            weight_index = NUM_INPUT_BLOCKS
                        else:
                            m = re_out.search(key)
                            if m:
                                out_idx = int(m.groups()[0])
                                weight_index = NUM_INPUT_BLOCKS + NUM_MID_BLOCK + out_idx

                if weight_index >= NUM_TOTAL_BLOCKS:
                    print(f"{bcolors.FAIL}ERROR: illegal block index: {key}{bcolors.ENDC}")
                    return f"{bcolors.FAIL}ERROR: illegal block index: {key}{bcolors.ENDC}",*non4
                
                if weight_index >= 0 and useblocks:
                    current_alpha = weights_a[weight_index]
                    if usebeta: current_beta = weights_b[weight_index]
            else:
                count_target_of_basealpha = count_target_of_basealpha + 1

            if len(deep) > 0:
                skey = key + BLOCKID[weight_index+1]
                for d in deep:
                    if d.count(":") != 2 :continue
                    dbs,dws,dr = d.split(":")[0],d.split(":")[1],d.split(":")[2]
                    dbs = blocker(dbs)
                    dbs,dws = dbs.split(" "), dws.split(" ")
                    dbn,dbs = (True,dbs[1:]) if dbs[0] == "NOT" else (False,dbs)
                    dwn,dws = (True,dws[1:]) if dws[0] == "NOT" else (False,dws)
                    flag = dbn
                    for db in dbs:
                        if db in skey:
                            flag = not dbn
                    if flag:flag = dwn
                    else:continue
                    for dw in dws:
                        if dw in skey:
                            flag = not dwn
                    if flag:
                        dr = eratiodealer(dr,randomer,weight_index+1,num,lucks)
                        if deepprint :print(dbs,dws,key,dr)
                        current_alpha = dr

            keyratio.append([key,current_alpha, current_beta])
            #keyratio.append([key,current_alpha, current_beta,list(theta_0[key].shape),torch.sum(theta_0[key]).item(), torch.mean(theta_0[key]).item(), torch.max(theta_0[key]).item(),  torch.min(theta_0[key]).item()])

            if calcmode == "normal":
                if a.shape != b.shape and a.shape[0:1] + a.shape[2:] == b.shape[0:1] + b.shape[2:]:
                    # Merge only the vectors the models have in common.  Otherwise we get an error due to dimension mismatch.
                    theta_0_a = theta_0[key][:, 0:4, :, :]
                else:
                    theta_0_a = theta_0[key]

                if MODES[1] in mode:#Add
                    caster(f"model A[{key}] +  {current_alpha} + * (model B - model C)[{key}]",hear)
                    theta_0_a = theta_0_a + current_alpha * theta_1[key]
                elif MODES[2] in mode:#Triple
                    caster(f"model A[{key}] +  {1-current_alpha-current_beta} +  model B[{key}]*{current_alpha} + model C[{key}]*{current_beta}",hear)
                    theta_0_a = (1 - current_alpha-current_beta) * theta_0_a + current_alpha * theta_1[key]+current_beta * theta_2[key]
                elif MODES[3] in mode:#Twice
                    caster(f"model A[{key}] +  {1-current_alpha} + * model B[{key}]*{alpha}",hear)
                    caster(f"model A+B[{key}] +  {1-current_beta} + * model C[{key}]*{beta}",hear)
                    theta_0_a = (1 - current_alpha) * theta_0_a + current_alpha * theta_1[key]
                    theta_0_a = (1 - current_beta) * theta_0_a + current_beta * theta_2[key]
                else:#Weight
                    if current_alpha == 1:
                        caster(f"alpha = 1,model A[{key}=model B[{key}",hear)
                        theta_0_a = theta_1[key]
                    elif current_alpha !=0:
                        caster(f"model A[{key}] +  {1-current_alpha} + * (model B)[{key}]*{alpha}",hear)
                        theta_0_a = (1 - current_alpha) * theta_0_a + current_alpha * theta_1[key]

                if a.shape != b.shape and a.shape[0:1] + a.shape[2:] == b.shape[0:1] + b.shape[2:]:
                    theta_0[key][:, 0:4, :, :] = theta_0_a
                else:
                    theta_0[key] = theta_0_a

            elif calcmode == "cosineA": #favors modelA's structure with details from B
                # skip VAE model parameters to get better results
                if "first_stage_model" in key: continue
                if "model" in key and key in theta_0:
                    # Normalize the vectors before merging
                    theta_0_norm = nn.functional.normalize(theta_0[key].to(torch.float32), p=2, dim=0)
                    theta_1_norm = nn.functional.normalize(theta_1[key].to(torch.float32), p=2, dim=0)
                    simab = sim(theta_0_norm, theta_1_norm)
                    dot_product = torch.dot(theta_0_norm.view(-1), theta_1_norm.view(-1))
                    magnitude_similarity = dot_product / (torch.norm(theta_0_norm) * torch.norm(theta_1_norm))
                    combined_similarity = (simab + magnitude_similarity) / 2.0
                    k = (combined_similarity - sims.min()) / (sims.max() - sims.min())
                    k = k - abs(current_alpha)
                    k = k.clip(min=0,max=1.0)
                    caster(f"model A[{key}] {1-k} +  (model B)[{key}]*{k}",hear)
                    theta_0[key] = theta_1[key] * (1 - k) + theta_0[key] * k

            elif calcmode == "cosineB": #favors modelB's structure with details from A
                # skip VAE model parameters to get better results
                if "first_stage_model" in key: continue
                if "model" in key and key in theta_0:
                    simab = sim(theta_0[key].to(torch.float32), theta_1[key].to(torch.float32))
                    dot_product = torch.dot(theta_0[key].view(-1).to(torch.float32), theta_1[key].view(-1).to(torch.float32))
                    magnitude_similarity = dot_product / (torch.norm(theta_0[key].to(torch.float32)) * torch.norm(theta_1[key].to(torch.float32)))
                    combined_similarity = (simab + magnitude_similarity) / 2.0
                    k = (combined_similarity - sims.min()) / (sims.max() - sims.min())
                    k = k - current_alpha
                    k = k.clip(min=0,max=1.0)
                    caster(f"model A[{key}] *{1-k} + (model B)[{key}]*{k}",hear)
                    theta_0[key] = theta_1[key] * (1 - k) + theta_0[key] * k

            elif calcmode == "trainDifference":
                # Check if theta_1[key] is equal to theta_2[key]
                if torch.allclose(theta_1[key].float(), theta_2[key].float(), rtol=0, atol=0):
                    theta_2[key] = theta_0[key]
                    continue

                diff_AB = theta_1[key].float() - theta_2[key].float()

                distance_A0 = torch.abs(theta_1[key].float() - theta_2[key].float())
                distance_A1 = torch.abs(theta_1[key].float() - theta_0[key].float())

                sum_distances = distance_A0 + distance_A1

                scale = torch.where(sum_distances != 0, distance_A1 / sum_distances, torch.tensor(0.).float())
                sign_scale = torch.sign(theta_1[key].float() - theta_2[key].float())
                scale = sign_scale * torch.abs(scale)

                new_diff = scale * torch.abs(diff_AB)
                theta_0[key] = theta_0[key] + (new_diff * (current_alpha*1.8))

            elif calcmode == "smoothAdd":
                caster(f"model A[{key}] +  {current_alpha} + * (model B - model C)[{key}]", hear)
                # Apply median filter to the weight differences
                filtered_diff = scipy.ndimage.median_filter(theta_1[key].to(torch.float32).cpu().numpy(), size=3)
                # Apply Gaussian filter to the filtered differences
                filtered_diff = scipy.ndimage.gaussian_filter(filtered_diff, sigma=1)
                theta_1[key] = torch.tensor(filtered_diff)
                # Add the filtered differences to the original weights
                theta_0[key] = theta_0[key] + current_alpha * theta_1[key]

            elif calcmode == "smoothAdd MT":
                key_and_alpha[key] = current_alpha

            elif calcmode == "tensor":
                dim = theta_0[key].dim()
                if dim == 0 : continue
                if current_alpha+current_beta <= 1 :
                    talphas = int(theta_0[key].shape[0]*(current_beta))
                    talphae = int(theta_0[key].shape[0]*(current_alpha+current_beta))
                    if dim == 1:
                        theta_0[key][talphas:talphae] = theta_1[key][talphas:talphae].clone()

                    elif dim == 2:
                        theta_0[key][talphas:talphae,:] = theta_1[key][talphas:talphae,:].clone()

                    elif dim == 3:
                        theta_0[key][talphas:talphae,:,:] = theta_1[key][talphas:talphae,:,:].clone()

                    elif dim == 4:
                        theta_0[key][talphas:talphae,:,:,:] = theta_1[key][talphas:talphae,:,:,:].clone()

                else:
                    talphas = int(theta_0[key].shape[0]*(current_alpha+current_beta-1))
                    talphae = int(theta_0[key].shape[0]*(current_beta))
                    theta_t = theta_1[key].clone()
                    if dim == 1:
                        theta_t[talphas:talphae] = theta_0[key][talphas:talphae].clone()

                    elif dim == 2:
                        theta_t[talphas:talphae,:] = theta_0[key][talphas:talphae,:].clone()

                    elif dim == 3:
                        theta_t[talphas:talphae,:,:] = theta_0[key][talphas:talphae,:,:].clone()

                    elif dim == 4:
                        theta_t[talphas:talphae,:,:,:] = theta_0[key][talphas:talphae,:,:,:].clone()
                    theta_0[key] = theta_t

            elif calcmode == "tensor2":
                dim = theta_0[key].dim()
                if dim == 0 : continue
                if current_alpha+current_beta <= 1 :
                    talphas = int(theta_0[key].shape[0]*(current_beta))
                    talphae = int(theta_0[key].shape[0]*(current_alpha+current_beta))
                    if dim > 1:
                        if theta_0[key].shape[1] > 100:
                            talphas = int(theta_0[key].shape[1]*(current_beta))
                            talphae = int(theta_0[key].shape[1]*(current_alpha+current_beta))
                    if dim == 1:
                        theta_0[key][talphas:talphae] = theta_1[key][talphas:talphae].clone()

                    elif dim == 2:
                        theta_0[key][:,talphas:talphae] = theta_1[key][:,talphas:talphae].clone()

                    elif dim == 3:
                        theta_0[key][:,talphas:talphae,:] = theta_1[key][:,talphas:talphae,:].clone()

                    elif dim == 4:
                        theta_0[key][:,talphas:talphae,:,:] = theta_1[key][:,talphas:talphae,:,:].clone()

                else:
                    talphas = int(theta_0[key].shape[0]*(current_alpha+current_beta-1))
                    talphae = int(theta_0[key].shape[0]*(current_beta))
                    theta_t = theta_1[key].clone()
                    if dim > 1:
                        if theta_0[key].shape[1] > 100:
                            talphas = int(theta_0[key].shape[1]*(current_alpha+current_beta-1))
                            talphae = int(theta_0[key].shape[1]*(current_beta))
                    if dim == 1:
                        theta_t[talphas:talphae] = theta_0[key][talphas:talphae].clone()

                    elif dim == 2:
                        theta_t[:,talphas:talphae] = theta_0[key][:,talphas:talphae].clone()

                    elif dim == 3:
                        theta_t[:,talphas:talphae,:] = theta_0[key][:,talphas:talphae,:].clone()

                    elif dim == 4:
                        theta_t[:,talphas:talphae,:,:] = theta_0[key][:,talphas:talphae,:,:].clone()
                    theta_0[key] = theta_t

            if any(item in key for item in FINETUNES) and fine:
                index = FINETUNES.index(key)
                if 5 > index : 
                    theta_0[key] =theta_0[key]* fine[index] 
                else :theta_0[key] =theta_0[key] + torch.tensor(fine[5])

            # statistics["sum"][key] = [torch.sum(theta_0[key]).item()] if key not in statistics["sum"].keys() else statistics["sum"][key] + [torch.sum(theta_0[key]).item()]
            # statistics["mean"][key] = [torch.mean(theta_0[key]).item()] if key not in statistics["mean"].keys() else statistics["mean"][key] + [torch.mean(theta_0[key]).item()]
            # statistics["max"][key] = [torch.max(theta_0[key]).item()] if key not in statistics["max"].keys() else statistics["max"][key] + [torch.max(theta_0[key]).item()]
            # statistics["min"][key] = [torch.min(theta_0[key]).item()] if key not in statistics["min"].keys() else statistics["min"][key] + [torch.min(theta_0[key]).item()]

    if calcmode == "smoothAdd MT":
        # setting threads to higher than 8 doesn't significantly affect the time for merging
        threads = cpu_count()
        tasks_per_thread = 8

        theta_0, theta_1, stopped = multithread_smoothadd(key_and_alpha, theta_0, theta_1, threads, tasks_per_thread, hear)
        if stopped:
            return "STOPPED", *non4

    currentmodel = makemodelname(weights_a,weights_b,model_a, model_b,model_c, base_alpha,base_beta,useblocks,mode,calcmode)

    for key in tqdm(theta_1.keys(), desc="Stage 2/2"):
        if key in chckpoint_dict_skip_on_merge:
            continue
        if "model" in key and key not in theta_0:
            theta_0.update({key:theta_1[key]})

    del theta_1

    if calcmode == "trainDifference":
        del theta_2

    bake_in_vae_filename = sd_vae.vae_dict.get(bake_in_vae, None)
    if bake_in_vae_filename is not None:
        print(f"Baking in VAE from {bake_in_vae_filename}")
        vae_dict = sd_vae.load_vae_dict(bake_in_vae_filename, map_location='cpu')

        for key in vae_dict.keys():
            theta_0_key = 'first_stage_model.' + key
            if theta_0_key in theta_0:
                theta_0[theta_0_key] = vae_dict[key]

        del vae_dict

    modelid = rwmergelog(currentmodel,mergedmodel)
    if "save E-list" in lucks["set"]: saveekeys(keyratio,modelid)

    caster(mergedmodel,False)

    if save_metadata:
        merge_recipe = {
            "type": "sd-webui-supermerger",
            "weights_alpha": weights_a if useblocks else None,
            "weights_beta": weights_b if useblocks else None,
            "weights_alpha_orig": weights_a_orig if useblocks else None,
            "weights_beta_orig": weights_b_orig if useblocks else None,
            "model_a": longhashfromname(model_a),
            "model_b": longhashfromname(model_b),
            "model_c": longhashfromname(model_c),
            "base_alpha": base_alpha,
            "base_beta": base_beta,
            "mode": mode,
            "mbw": useblocks,
            "elemental_merge": deep,
            "calcmode" : calcmode
        }
        metadata["sd_merge_recipe"] = json.dumps(merge_recipe)
        metadata["sd_merge_models"] = {}

        def add_model_metadata(checkpoint_name):
            checkpoint_info = sd_models.get_closet_checkpoint_match(checkpoint_name)
            checkpoint_info.calculate_shorthash()
            metadata["sd_merge_models"][checkpoint_info.sha256] = {
                "name": checkpoint_name,
                "legacy_hash": checkpoint_info.hash
            }

            #metadata["sd_merge_models"].update(checkpoint_info.metadata.get("sd_merge_models", {}))

        if model_a:
            add_model_metadata(model_a)
        if model_b:
            add_model_metadata(model_b)
        if model_c:
            add_model_metadata(model_c)

        metadata["sd_merge_models"] = json.dumps(metadata["sd_merge_models"])

    return "",currentmodel,modelid,theta_0,metadata


def multithread_smoothadd(key_and_alpha, theta_0, theta_1, threads, tasks_per_thread, hear):  
    lock_theta_0 = Lock()
    lock_theta_1 = Lock()
    lock_progress = Lock()

    def thread_callback(keys):
        nonlocal theta_0, theta_1

        if stopmerge:
            return False

        for key in keys:
            caster(f"model A[{key}] +  {key_and_alpha[key]} + * (model B - model C)[{key}]", hear)
            filtered_diff = scipy.ndimage.median_filter(theta_1[key].to(torch.float32).cpu().numpy(), size=3)
            filtered_diff = scipy.ndimage.gaussian_filter(filtered_diff, sigma=1)
            with lock_theta_1:
                theta_1[key] = torch.tensor(filtered_diff)
            with lock_theta_0:
                theta_0[key] = theta_0[key] + key_and_alpha[key] * theta_1[key]

        with lock_progress:
            progress.update(len(keys))

        return True

    def extract_and_remove(input_list, count):
        extracted = input_list[:count]
        del input_list[:count]

        return extracted

    keys = list(key_and_alpha.keys())

    total_threads = ceil(len(keys) / int(tasks_per_thread))
    print(f"max threads = {threads}, total threads = {total_threads}, tasks per thread = {tasks_per_thread}")

    progress = tqdm(key_and_alpha.keys(), desc="smoothAdd MT")

    futures = []
    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [executor.submit(thread_callback, extract_and_remove(keys, int(tasks_per_thread))) for i in range(total_threads)]

        for future in as_completed(futures):
            if not future.result():
                executor.shutdown()
                return theta_0, theta_1, True

        del progress

    return theta_0, theta_1, False

def forkforker(filename):
    try:
        return sd_models.read_state_dict(filename,"cuda")
    except:
        return sd_models.read_state_dict(filename)

def load_model_weights_m(model,model_a,model_b,save):
    checkpoint_info = sd_models.get_closet_checkpoint_match(model)
    sd_model_name = checkpoint_info.model_name

    cachenum = shared.opts.sd_checkpoint_cache
    
    if save:        
        if model_a:
            load_model(checkpoint_info)
        print(f"Loading weights [{sd_model_name}] from file")
        return forkforker(checkpoint_info.filename)

    if checkpoint_info in checkpoints_loaded:
        print(f"Loading weights [{sd_model_name}] from cache")
        return checkpoints_loaded[checkpoint_info]
    elif cachenum>0 and model_a:
        load_model(checkpoint_info)
        print(f"Loading weights [{sd_model_name}] from cache")
        return checkpoints_loaded[checkpoint_info]
    elif cachenum>1 and model_b:
        load_model(checkpoint_info)
        print(f"Loading weights [{sd_model_name}] from cache")
        return checkpoints_loaded[checkpoint_info]
    elif cachenum>2:
        load_model(checkpoint_info)
        print(f"Loading weights [{sd_model_name}] from cache")
        return checkpoints_loaded[checkpoint_info]
    else:
        if model_a:
            load_model(checkpoint_info)
        print(f"Loading weights [{sd_model_name}] from file")
        return forkforker(checkpoint_info.filename)

def makemodelname(weights_a,weights_b,model_a, model_b,model_c, alpha,beta,useblocks,mode,calc):
    model_a=filenamecutter(model_a)
    model_b=filenamecutter(model_b)
    model_c=filenamecutter(model_c)

    if type(alpha) == str:alpha = float(alpha)
    if type(beta)== str:beta  = float(beta)

    if useblocks:
        if MODES[1] in mode:#add
            currentmodel =f"{model_a} + ({model_b} - {model_c}) x alpha ({str(round(alpha,3))},{','.join(str(s) for s in weights_a)})"
        elif MODES[2] in mode:#triple
            currentmodel =f"{model_a} x (1-alpha-beta) + {model_b} x alpha + {model_c} x beta (alpha = {str(round(alpha,3))},{','.join(str(s) for s in weights_a)},beta = {beta},{','.join(str(s) for s in weights_b)})"
        elif MODES[3] in mode:#twice
            currentmodel =f"({model_a} x (1-alpha) + {model_b} x alpha)x(1-beta)+  {model_c} x beta ({str(round(alpha,3))},{','.join(str(s) for s in weights_a)})_({str(round(beta,3))},{','.join(str(s) for s in weights_b)})"
        else:
            currentmodel =f"{model_a} x (1-alpha) + {model_b} x alpha ({str(round(alpha,3))},{','.join(str(s) for s in weights_a)})"
    else:
        if MODES[1] in mode:#add
            currentmodel =f"{model_a} + ({model_b} -  {model_c}) x {str(round(alpha,3))}"
        elif MODES[2] in mode:#triple
            currentmodel =f"{model_a} x {str(round(1-alpha-beta,3))} + {model_b} x {str(round(alpha,3))} + {model_c} x {str(round(beta,3))}"
        elif MODES[3] in mode:#twice
            currentmodel =f"({model_a} x {str(round(1-alpha,3))} +{model_b} x {str(round(alpha,3))}) x {str(round(1-beta,3))} + {model_c} x {str(round(beta,3))}"
        else:
            currentmodel =f"{model_a} x {str(round(1-alpha,3))} + {model_b} x {str(round(alpha,3))}"
    if calc != "normal":
        currentmodel = currentmodel + "_" + calc
        if calc == "tensor":
            currentmodel = currentmodel + f"_beta_{beta}"
    return currentmodel

path_root = scripts.basedir()

def rwmergelog(mergedname = "",settings= [],id = 0):
    setting = settings.copy()
    filepath = os.path.join(path_root, "mergehistory.csv")
    is_file = os.path.isfile(filepath)
    if not is_file:
        with open(filepath, 'a') as f:
                                       #msettings=[0 weights_a,1 weights_b,2 model_a,3 model_b,4 model_c,5 base_alpha,6 base_beta,7 mode,8 useblocks,9 custom_name,10 save_sets,11 id_sets, 12 deep 13 calcmode]
            f.writelines('"ID","time","name","weights alpha","weights beta","model A","model B","model C","alpha","beta","mode","use MBW","plus lora","custum name","save setting","use ID"\n')
    with  open(filepath, 'r+') as f:
        reader = csv.reader(f)
        mlist = [raw for raw in reader]
        if mergedname != "":
            mergeid = len(mlist)
            setting.insert(0,mergedname)
            for i,x in enumerate(setting):
                if "," in str(x) or "\n" in str(x):setting[i] = f'"{str(setting[i])}"'
            text = ",".join(map(str, setting))
            text=str(mergeid)+","+datetime.datetime.now().strftime('%Y.%m.%d %H.%M.%S.%f')[:-7]+"," + text + "\n"
            f.writelines(text)
            return mergeid
        try:
            out = mlist[int(id)]
        except:
            out = "ERROR: OUT of ID index"
        return out

def saveekeys(keyratio,modelid):
    import csv
    path_root = scripts.basedir()
    dir_path = os.path.join(path_root,"extensions","sd-webui-supermerger","scripts", "data")

    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
    
    filepath = os.path.join(dir_path,f"{modelid}.csv")

    with open(filepath, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(keyratio)

def savestatics(modelid):
    for key in statistics.keys():
        result = [[tkey] + list(statistics[key][tkey]) for tkey in statistics[key].keys()]
        saveekeys(result,f"{modelid}_{key}")

def get_font(fontsize):
    path_root = scripts.basedir()
    fontpath = os.path.join(path_root,"extensions","sd-webui-supermerger","scripts", "Roboto-Regular.ttf")
    try:
        return ImageFont.truetype(opts.font or fontpath, fontsize)
    except Exception:
        return ImageFont.truetype(fontpath, fontsize)

def draw_origin(grid, text,width,height,width_one):
    grid_d= Image.new("RGB", (grid.width,grid.height), "white")
    grid_d.paste(grid,(0,0))

    d= ImageDraw.Draw(grid_d)
    color_active = (0, 0, 0)
    fontsize = (width+height)//25
    fnt = get_font(fontsize)

    if grid.width != width_one:
        while d.multiline_textsize(text, font=fnt)[0] > width_one*0.75 and fontsize > 0:
            fontsize -=1
            fnt = get_font(fontsize)
    d.multiline_text((0,0), text, font=fnt, fill=color_active,align="center")
    return grid_d

def wpreseter(w,presets):
    if "," not in w and w != "":
        presets=presets.splitlines()
        wdict={}
        for l in presets:
            if ":" in l :
                key = l.split(":",1)[0]
                wdict[key.strip()]=l.split(":",1)[1]
            if "\t" in l:
                key = l.split("\t",1)[0]
                wdict[key.strip()]=l.split("\t",1)[1]
        if w.strip() in wdict:
            name = w
            w = wdict[w.strip()]
            print(f"weights {name} imported from presets : {w}")
    return w

def fullpathfromname(name):
    if hash == "" or hash ==[]: return ""
    checkpoint_info = sd_models.get_closet_checkpoint_match(name)
    return checkpoint_info.filename

def namefromhash(hash):
    if hash == "" or hash ==[]: return ""
    checkpoint_info = sd_models.get_closet_checkpoint_match(hash)
    return checkpoint_info.model_name

def hashfromname(name):
    from modules import sd_models
    if name == "" or name ==[]: return ""
    checkpoint_info = sd_models.get_closet_checkpoint_match(name)
    if checkpoint_info.shorthash is not None:
        return checkpoint_info.shorthash
    return checkpoint_info.calculate_shorthash()

def longhashfromname(name):
    from modules import sd_models
    if name == "" or name ==[]: return ""
    checkpoint_info = sd_models.get_closet_checkpoint_match(name)
    if checkpoint_info.sha256 is not None:
        return checkpoint_info.sha256
    checkpoint_info.calculate_shorthash()
    return checkpoint_info.sha256

RANCHA = ["R","U","X"]

def randdealer(w:str,randomer,ab,lucks,deep):
    up,low = lucks["upp"],lucks["low"]
    up,low = (up.split(","),low.split(","))
    out = []
    outd = {"R":[],"U":[],"X":[]}
    add = RANDMAP[ab]
    for i, r in enumerate (w.split(",")):
        if r.strip() =="R":
            out.append(str(round(randomer[i+add],lucks["round"])))
        elif r.strip() == "U":
            out.append(str(round(-2 * randomer[i+add] + 1.5,lucks["round"])))
        elif r.strip() == "X":
            out.append(str(round((float(low[i])-float(up[i]))* randomer[i+add] + float(up[i]),lucks["round"])))
        elif "E" in r:
            key = r.strip().replace("E","")
            outd[key].append(BLOCKID[i])
            out.append("0")
        else:
            out.append(r)
    for key in outd.keys():
        if outd[key] != []:
            deep = deep + f",{' '.join(outd[key])}::{key}" if deep else f"{' '.join(outd[key])}::{key}"
    return ",".join(out), deep

def eratiodealer(dr,randomer,block,num,lucks):
    if  any(element in dr for element in RANCHA):
        up,low = lucks["upp"],lucks["low"]
        up,low = (up.split(","),low.split(","))
        add = RANDMAP[2]
        if dr.strip() =="R":
            return round(randomer[num+add],lucks["round"])
        elif dr.strip() == "U":
            return round(-2 * randomer[num+add] + 1,lucks["round"])
        elif dr.strip() == "X":
            return round((float(low[block])-float(up[block]))* randomer[num+add] + float(up[block]),lucks["round"])
    else:
        return float(dr)

def simggen(prompt, nprompt, steps, sampler, cfg, seed, w, h,genoptions,hrupscaler,hr2ndsteps,denoise_str,hr_scale,
                   s_prompt,s_nprompt,s_steps,s_sampler,s_cfg,s_seed,s_w,s_h,batch_size,mergeinfo="",id_sets=[],modelid = "no id"):
    shared.state.begin()
    p = processing.StableDiffusionProcessingTxt2Img(
        sd_model=shared.sd_model,
        do_not_save_grid=True,
        do_not_save_samples=True,
        do_not_reload_embeddings=True,
    )
    p.batch_size = int(batch_size)
    p.prompt = prompt if s_prompt == "" else s_prompt
    p.negative_prompt = nprompt if s_nprompt == "" else s_nprompt
    p.steps = steps if s_steps == 0 else s_steps
    try:
        p.sampler_name = sd_samplers.samplers[sampler].name if s_sampler == 0 or s_sampler == None else sd_samplers.samplers[s_sampler-1].name
    except:
        print(f"{bcolors.Fail}Error:sampler:{sampler},s_sampler:{s_sampler}{bcolors.ENDC}")
    p.cfg_scale = cfg  if s_cfg == 0 else s_cfg
    p.seed = seed  if s_seed == 0 else s_seed
    p.width = w  if s_w == 0 else s_w
    p.height = h  if s_h == 0 else s_h
    p.seed_resize_from_w=0
    p.seed_resize_from_h=0
    p.denoising_strength=None

    p.cached_c = [None,None]
    p.cached_uc = [None,None]

    p.cached_hr_c = [None, None]
    p.cached_hr_uc = [None, None]

    #"Restore faces", "Tiling", "Hires. fix"

    if "Hires. fix" in genoptions:
        p.enable_hr = True
        p.denoising_strength = denoise_str
        p.hr_upscaler = hrupscaler
        p.hr_second_pass_steps = hr2ndsteps
        p.hr_scale = hr_scale
    
    if "Tiling" in genoptions:
        p.tiling = True

    if "Restore faces" in genoptions:
        p.restore_faces = True

    if type(p.prompt) == list:
        p.all_prompts = [shared.prompt_styles.apply_styles_to_prompt(x, p.styles) for x in p.prompt]
    else:
        p.all_prompts = [shared.prompt_styles.apply_styles_to_prompt(p.prompt, p.styles)]

    if type(p.negative_prompt) == list:
        p.all_negative_prompts = [shared.prompt_styles.apply_negative_styles_to_prompt(x, p.styles) for x in p.negative_prompt]
    else:
        p.all_negative_prompts = [shared.prompt_styles.apply_negative_styles_to_prompt(p.negative_prompt, p.styles)]

    processed:Processed = processing.process_images(p)
    if "image" in id_sets:
        for i, image in enumerate(processed.images):
            processed.images[i] = draw_origin(image, str(modelid),w,h,w)

    if "PNG info" in id_sets:mergeinfo = mergeinfo + " ID " + str(modelid)

    infotext = create_infotext(p, p.all_prompts, p.all_seeds, p.all_subseeds)
    if infotext.count("Steps: ")>1:
        infotext = infotext[:infotext.rindex("Steps")]

    infotexts = infotext.split(",")
    for i,x in enumerate(infotexts):
        if "Model:"in x:
            infotexts[i] = " Model: "+mergeinfo.replace(","," ")
    infotext= ",".join(infotexts)

    for i, image in enumerate(processed.images):
        images.save_image(image, opts.outdir_txt2img_samples, "",p.seed, p.prompt,shared.opts.samples_format, p=p,info=infotext)

    if batch_size > 1:
        grid = images.image_grid(processed.images, p.batch_size)
        processed.images.insert(0, grid)
        images.save_image(grid, opts.outdir_txt2img_grids, "grid", p.seed, p.prompt, opts.grid_format, info=infotext, short_filename=not opts.grid_extended_filename, p=p, grid=True)
    shared.state.end()
    return processed.images,infotext,plaintext_to_html(processed.info), plaintext_to_html(processed.comments),p

def blocker(blocks):
    blocks = blocks.split(" ")
    output = ""
    for w in blocks:
        flagger=[False]*26
        changer = True
        if "-" in w:
            wt = [wt.strip() for wt in w.split('-')]
            if  BLOCKID.index(wt[1]) > BLOCKID.index(wt[0]):
                flagger[BLOCKID.index(wt[0]):BLOCKID.index(wt[1])+1] = [changer]*(BLOCKID.index(wt[1])-BLOCKID.index(wt[0])+1)
            else:
                flagger[BLOCKID.index(wt[1]):BLOCKID.index(wt[0])+1] = [changer]*(BLOCKID.index(wt[0])-BLOCKID.index(wt[1])+1)
        else:
            output = output + " " + w if output else w
        for i in range(26):
            if flagger[i]: output = output + " " + BLOCKID[i] if output else BLOCKID[i]
    return output

def fineman(fine):
    fine = [
        1 - fine[0] * 0.01,
        1+ fine[0] * 0.02,
        1 - fine[1] * 0.01,
        1+ fine[1] * 0.02,
        1 - fine[2] * 0.01,
        [x*0.02 for x in fine[3:]]
                ]
    return fine

FINETUNES = [
"model.diffusion_model.input_blocks.0.0.weight",
"model.diffusion_model.input_blocks.0.0.bias",
"model.diffusion_model.out.0.weight",
"model.diffusion_model.out.0.bias",
"model.diffusion_model.out.2.weight",
"model.diffusion_model.out.2.bias",
]
